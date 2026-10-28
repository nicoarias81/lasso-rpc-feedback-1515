#!/usr/bin/env python3
"""Repro for Lasso feedback, Base mainnet, 2026-10-28.

Usage:
    export LASSO_KEY=<key from POST https://lasso.sh/api/v1/keys>
    python3 repro.py

Checks, in order:
  1. latency p50/p95 of lasso premium+public vs publicnode/base.org (n=15, eth_chainId)
  2. the `rpc_url` returned by POST /api/v1/keys is not callable as-is (422)
  3. state agreement at a pinned block across all four endpoints
  4. eth_getLogs: 10-block range vs 200k-block range (see retries / log_range_limit)
"""
import json, os, statistics, time, urllib.error, urllib.request

KEY = os.environ["LASSO_KEY"]
ROOT = f"https://lasso.sh/rpc/k/{KEY}/profile"
URLS = {
    "lasso-premium": f"{ROOT}/premium/base",
    "lasso-public": f"{ROOT}/public/base",
    "publicnode": "https://base-rpc.publicnode.com",
    "base-org": "https://mainnet.base.org",
}
HDR = {"content-type": "application/json", "user-agent": "hermes-agent/1.0"}
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WALLET = "0x5555Fa783936c260f77385b4e153b9725fef1719"


def rpc(name_or_url, method, params, meta=False):
    url = URLS.get(name_or_url, name_or_url)
    if meta and name_or_url.startswith("lasso"):
        url += "?include_meta=body"
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=HDR)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body, code = json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"_raw": raw[:200].decode("utf-8", "replace")}
        code = e.code
    return body, (time.perf_counter() - t) * 1000, code


def bench(target, n=15):
    ts = sorted(rpc(target, "eth_chainId", [])[1] for _ in range(n))
    return {"p50_ms": round(statistics.median(ts), 1), "p95_ms": round(ts[int(n * 0.95) - 1], 1)}


def main():
    print("== 1. latency (n=15, eth_chainId) ==")
    print(json.dumps({k: bench(k) for k in URLS}, indent=1))

    print("\n== 2. bare rpc_url from POST /api/v1/keys ==")
    bare, ms, code = rpc(f"{ROOT}/premium", "eth_chainId", [])
    print(f"HTTP {code} in {ms:.0f}ms -> {json.dumps(bare)[:220]}")
    ok, _, code2 = rpc("lasso-premium", "eth_chainId", [])
    print(f"with /base: HTTP {code2} -> {json.dumps(ok.get('result'))}")

    print("\n== 3. agreement at a pinned block ==")
    head = rpc("publicnode", "eth_blockNumber", [])[0]
    if "result" not in head:
        head = rpc("lasso-premium", "eth_blockNumber", [])[0]
    blk = int(head["result"], 16) - 5
    print("block:", blk)
    cases = [
        ("eth_getBalance", [WALLET, hex(blk)]),
        ("eth_getCode", [WALLET, hex(blk)]),
        ("eth_call", [{"to": USDC, "data": "0x70a08231" + "00" * 12 + WALLET[2:].lower()}, hex(blk)]),
    ]
    for method, params in cases:
        vals = {k: json.dumps(rpc(k, method, params)[0].get("result", "ERR"))[:64] for k in URLS}
        print(f"{method}: all four agree = {len(set(vals.values())) == 1}")
        for k, v in vals.items():
            print(f"   {k:14} {v}")

    print("\n== 4. eth_getLogs ==")
    for span, label in ((10, "10 blocks"), (200_000, "200k blocks")):
        d, ms, _ = rpc("lasso-premium", "eth_getLogs",
                       [{"address": USDC, "fromBlock": hex(blk - span), "toBlock": hex(blk)}], meta=True)
        meta = d.get("lasso_meta") or {}
        count = len(d["result"]) if "result" in d else None
        print(f"{label:11} {ms:6.0f}ms logs={count} retries={meta.get('retries')} "
              f"provider={(meta.get('selected_provider') or {}).get('id')} "
              f"error={json.dumps(d.get('error', {}))[:120]}")


if __name__ == "__main__":
    main()
