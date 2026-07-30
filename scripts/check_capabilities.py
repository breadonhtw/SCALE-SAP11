#!/usr/bin/env python3
"""Probe tenant capabilities for the TrustSphere prototype (CLAUDE.md §20).

Tests each gated dependency against the real tenant and prints PASS/FAIL with
evidence. Results are recorded by hand in docs/capability-matrix.md — this
script is the proof-of-test, per the truthfulness discipline in CLAUDE.md §26.

Usage:
    python scripts/check_capabilities.py
"""

import json
import os
import urllib.parse
import urllib.request

from hdbcli import dbapi

CREDS_PATH = os.environ.get(
    "TEAM11_CREDS", "/Users/brandon/Desktop/SAP/team-11/team_11_credentials.json"
)

RESULTS = []


def record(capability, ok, evidence):
    RESULTS.append((capability, ok, evidence))
    print(f"{'✅' if ok else '❌'} {capability}: {evidence}")


def hana_conn(creds):
    db = creds["database"]
    return dbapi.connect(
        address=db["host"],
        port=db["port"],
        user=db["username"],
        password=db["password"],
        encrypt=True,
        sslValidateCertificate=False,
    )


def try_sql(cur, sql):
    try:
        cur.execute(sql)
        return True, cur.fetchall()
    except Exception as e:
        return False, str(e)[:200]


def main():
    with open(CREDS_PATH) as f:
        creds = json.load(f)

    conn = hana_conn(creds)
    cur = conn.cursor()

    # --- 1. HANA base ---
    ok, r = try_sql(cur, "SELECT VERSION FROM M_DATABASE")
    record("HANA Cloud connectivity", ok, f"version {r[0][0]}" if ok else r)

    # --- 2. Vector engine ---
    ok, r = try_sql(cur, "SELECT TO_REAL_VECTOR('[0.1,0.2,0.3]') FROM DUMMY")
    record("Vector: REAL_VECTOR type", ok, "TO_REAL_VECTOR works" if ok else r)
    ok, r = try_sql(
        cur,
        "SELECT COSINE_SIMILARITY(TO_REAL_VECTOR('[1,0]'), TO_REAL_VECTOR('[0,1]')) FROM DUMMY",
    )
    record("Vector: COSINE_SIMILARITY", ok, f"result {r[0][0]}" if ok else r)
    ok, r = try_sql(
        cur,
        "SELECT VECTOR_EMBEDDING('test', 'DOCUMENT', 'SAP_NEB.20240715') FROM DUMMY",
    )
    record("Vector: in-DB VECTOR_EMBEDDING (NEB model)", ok,
           "embedding function available" if ok else r)

    # --- 3. Graph / SPARQL ---
    ok, r = try_sql(cur, "SELECT COUNT(*) FROM SYS.GRAPH_WORKSPACES")
    record("Graph: GRAPH_WORKSPACES catalog", ok,
           f"{r[0][0]} workspaces visible" if ok else r)
    # privilege test: create a tiny workspace over our own tables, then drop
    try:
        for stmt in [
            'CREATE COLUMN TABLE TEAM_11_USER."_CAP_V" (ID INT PRIMARY KEY)',
            'CREATE COLUMN TABLE TEAM_11_USER."_CAP_E" (ID INT PRIMARY KEY, S INT NOT NULL, T INT NOT NULL)',
            '''CREATE GRAPH WORKSPACE TEAM_11_USER."_CAP_G"
               EDGE TABLE TEAM_11_USER."_CAP_E" SOURCE COLUMN S TARGET COLUMN T KEY COLUMN ID
               VERTEX TABLE TEAM_11_USER."_CAP_V" KEY COLUMN ID''',
        ]:
            cur.execute(stmt)
        record("Graph: CREATE GRAPH WORKSPACE privilege", True, "created and dropping test workspace")
    except Exception as e:
        record("Graph: CREATE GRAPH WORKSPACE privilege", False, str(e)[:200])
    finally:
        for stmt in [
            'DROP GRAPH WORKSPACE TEAM_11_USER."_CAP_G"',
            'DROP TABLE TEAM_11_USER."_CAP_E"',
            'DROP TABLE TEAM_11_USER."_CAP_V"',
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass
    ok, r = try_sql(cur, "CALL SPARQL_EXECUTE('SELECT * WHERE {?s ?p ?o} LIMIT 1', '', ?, ?)")
    record("SPARQL: knowledge-graph engine", ok, "SPARQL_EXECUTE callable" if ok else r)

    # --- 4. PAL / APL ---
    ok, r = try_sql(
        cur,
        "SELECT COUNT(*) FROM SYS.PROCEDURES WHERE SCHEMA_NAME='_SYS_AFL' AND PROCEDURE_NAME LIKE 'PAL_%'",
    )
    record("PAL: procedures visible in _SYS_AFL", ok and r[0][0] > 0,
           f"{r[0][0]} PAL procedures" if ok else r)
    ok, r = try_sql(
        cur,
        "SELECT ROLE_NAME FROM SYS.GRANTED_ROLES WHERE GRANTEE=CURRENT_USER AND ROLE_NAME LIKE '%AFL%'",
    )
    record("PAL: AFL execution role granted", ok and len(r) > 0,
           f"roles: {[x[0] for x in r]}" if ok and r else "no AFL role" if ok else r)
    try:
        import hana_ml
        record("hana_ml package", True, f"version {hana_ml.__version__}")
    except ImportError as e:
        record("hana_ml package", False, str(e))

    conn.close()

    # --- 5. AI Core ---
    ai = creds["ai_core"]
    try:
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": ai["client_id"],
            "client_secret": ai["client_secret"],
        }).encode()
        req = urllib.request.Request(ai["auth_url"] + "/oauth/token", data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            token = json.loads(resp.read())["access_token"]
        record("AI Core: OAuth", True, "token obtained")

        def ai_get(path):
            req = urllib.request.Request(
                ai["api_url"] + path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "AI-Resource-Group": ai["resource_group"],
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())

        deps = ai_get("/v2/lm/deployments")
        models = []
        for d in deps.get("resources", []):
            detail = d.get("details", {}).get("resources", {}).get("backend_details", {})
            model = detail.get("model", {})
            models.append(
                f"{model.get('name', d.get('scenarioId', '?'))}:{model.get('version', '')} "
                f"[{d.get('status')}] id={d.get('id')}"
            )
        record("AI Core: deployments", len(models) > 0,
               f"{len(models)} deployment(s): " + "; ".join(models) if models else "none in resource group")
    except Exception as e:
        record("AI Core: API", False, str(e)[:200])

    print("\n--- summary ---")
    for cap, ok, _ in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {cap}")


if __name__ == "__main__":
    main()
