from solscout.models import Pair
from solscout.sources import dexscreener, geckoterminal, rpc_safety

DS_PAIR = {
    "chainId": "solana", "dexId": "raydium", "pairAddress": "PairAddr1", "url": "https://dexscreener.com/solana/PairAddr1",
    "baseToken": {"address": "MintA", "name": "Alpha", "symbol": "ALPHA"},
    "priceUsd": "0.00123", "txns": {"m5": {"buys": 10, "sells": 5}, "h1": {"buys": 100, "sells": 60}, "h24": {"buys": 900, "sells": 700}},
    "volume": {"h24": 100000, "h6": 40000, "h1": 12000, "m5": 1500},
    "priceChange": {"m5": 1.5, "h1": 12, "h6": 30, "h24": 80},
    "liquidity": {"usd": 52000}, "fdv": 900000, "pairCreatedAt": 1_799_990_000_000,
}


def test_parse_dexscreener_pair():
    p = Pair.from_dexscreener(DS_PAIR)
    assert p.mint == "MintA" and p.price_usd == 0.00123 and p.liquidity_usd == 52000
    assert p.buys_h1 == 100 and p.sells_h1 == 60 and p.change_h1 == 12 and p.created_at == 1_799_990_000


def test_parse_rejects_other_chains_junk_and_zero_price():
    assert Pair.from_dexscreener({**DS_PAIR, "chainId": "ethereum"}) is None
    assert Pair.from_dexscreener({**DS_PAIR, "priceUsd": "0"}) is None
    assert Pair.from_dexscreener({"chainId": "solana"}) is None
    assert Pair.from_dexscreener("nope") is None


def test_parse_tolerates_missing_fields():
    p = Pair.from_dexscreener({"chainId": "solana", "baseToken": {"address": "M"}, "priceUsd": "1"})
    assert p.liquidity_usd == 0 and p.buys_h1 == 0 and p.created_at is None


def test_best_pair_per_mint_is_the_most_liquid():
    small = {**DS_PAIR, "pairAddress": "small", "liquidity": {"usd": 1000}}
    out = dexscreener.best_pairs([small, DS_PAIR])
    assert len(out) == 1 and out[0].pair_address == "PairAddr1"


def test_parse_candidates_filters_chain():
    got = dexscreener.parse_candidates([{"chainId": "solana", "tokenAddress": "A"}, {"chainId": "base", "tokenAddress": "B"}, {"x": 1}])
    assert got == ["A"]


def test_parse_geckoterminal_pools():
    payload = {"data": [
        {"relationships": {"base_token": {"data": {"id": "solana_MintX"}}}},
        {"relationships": {"base_token": {"data": {"id": "eth_0xabc"}}}},
        {"relationships": {}},
    ]}
    assert geckoterminal.parse_pools(payload) == ["MintX"]
    assert geckoterminal.parse_pools(None) == [] and geckoterminal.parse_pools({"data": "x"}) == []


MINT_INFO = {"owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
             "data": {"parsed": {"type": "mint", "info": {"mintAuthority": None, "freezeAuthority": None}}}}


def test_parse_safety_computes_concentration_excluding_the_largest():
    largest = [{"amount": "400"}] + [{"amount": "30"}] * 9 + [{"amount": "5"}]
    s = rpc_safety.parse_safety(MINT_INFO, largest, 1000)
    assert s.checked and s.mint_authority is None and s.freeze_authority is None and not s.token_2022
    assert s.largest_pct == 40.0 and s.top_holders_pct == 27.0


def test_parse_safety_reports_authorities_and_token_2022():
    info = {"owner": rpc_safety.TOKEN_2022,
            "data": {"parsed": {"type": "mint", "info": {"mintAuthority": "A", "freezeAuthority": "B"}}}}
    s = rpc_safety.parse_safety(info, [], 1000)
    assert s.mint_authority == "A" and s.freeze_authority == "B" and s.token_2022 and s.top_holders_pct is None


def test_parse_safety_rejects_non_mint():
    assert not rpc_safety.parse_safety({"data": {"parsed": {"type": "account"}}}, [], 1).checked
    assert not rpc_safety.parse_safety(None, [], 1).checked
