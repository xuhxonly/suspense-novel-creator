#!/usr/bin/env python3
"""只读 A 股行情 MCP Server。

数据源：akshare（开源，无需注册/账号/密钥）。行情主源为东方财富，被限流时
自动降级到新浪源。本服务不提供任何账户、委托、下单、撤单或资金类工具，
仅供行情查询与复盘。

接入方式（stdio MCP，配置到任意支持 MCP 的 AI 客户端）：
    {
      "mcpServers": {
        "astock-market": {
          "command": "python3",
          "args": ["/workspace/ths-mcp/server.py"]
        }
      }
    }

依赖安装：pip3 install -r /workspace/ths-mcp/requirements.txt
"""

import functools
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("astock-market")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")

SPOT_TTL_EM = 120    # 东财全表缓存秒数
SPOT_TTL_SINA = 300  # 新浪全表较慢，缓存更久

# 统一列名：所有行情工具都基于这张标准化表工作
SPOT_COLS = ["代码", "名称", "最新价", "涨跌幅", "今开", "最高", "最低", "换手率", "量比", "成交量", "成交额"]

_spot_cache = {"df": None, "ts": 0.0, "source": ""}
_spot_lock = threading.Lock()


def _safe(fn):
    """网络查询统一兜底：失败时把原因作为文本返回给 AI，而不是抛异常断连。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return f"查询失败：{type(e).__name__}: {e}"

    return wrapper


def _norm_code(c: str) -> str:
    return str(c).strip().zfill(6)


def _pick_or_nan(df: pd.DataFrame, name: str) -> pd.Series:
    for c in (name,):
        if c in df.columns:
            return df[c]
    return pd.Series([math.nan] * len(df))


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    code_col = "代码" if "代码" in df.columns else df.columns[0]
    name_col = "名称" if "名称" in df.columns else df.columns[1]
    out = pd.DataFrame({
        "代码": df[code_col].astype(str).str.strip().str[-6:],
        "名称": df[name_col],
    })
    for c in SPOT_COLS[2:]:
        out[c] = pd.to_numeric(_pick_or_nan(df, c), errors="coerce")
    return out[SPOT_COLS]


def _fetch_spot_em() -> pd.DataFrame:
    return ak.stock_zh_a_spot_em()


def _fetch_spot_sina() -> pd.DataFrame:
    df = ak.stock_zh_a_spot_sina()  # 分页拉取，较慢，仅作限流兜底
    if "代码" in df.columns:
        df = df.copy()
        df["代码"] = df["代码"].astype(str).str.strip().str[-6:]
    return df


def _get_spot_df() -> tuple[pd.DataFrame, str]:
    """返回 (标准化行情全表, 来源标签)，东财失败自动切新浪。"""
    with _spot_lock:
        now = time.time()
        if _spot_cache["df"] is not None:
            ttl = SPOT_TTL_EM if _spot_cache["source"] == "eastmoney" else SPOT_TTL_SINA
            if now - _spot_cache["ts"] < ttl:
                return _spot_cache["df"], _spot_cache["source"]
        try:
            df = _normalize_spot(_fetch_spot_em())
            source = "eastmoney"
        except Exception:
            df = _normalize_spot(_fetch_spot_sina())
            source = "sina"
        _spot_cache.update(df=df, ts=now, source=source)
        return df, source


def _load_watchlist() -> list[str]:
    if not os.path.exists(WATCHLIST_PATH):
        return []
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f).get("codes", [])


def _save_watchlist(codes: list[str]) -> None:
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump({"codes": sorted(set(codes))}, f, ensure_ascii=False, indent=2)


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _pct(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return "-"


def _fmt_amount(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if math.isnan(f):
        return "-"
    if abs(f) >= 1e8:
        return f"{f / 1e8:.2f}亿"
    if abs(f) >= 1e4:
        return f"{f / 1e4:.2f}万"
    return f"{f:g}"


def _quote_lines(sub: pd.DataFrame) -> list[str]:
    lines = []
    for _, r in sub.iterrows():
        lines.append(
            f"{r['名称']}({r['代码']})  现价 {_cell(r['最新价'])}  涨跌 {_pct(r['涨跌幅'])}  "
            f"今开 {_cell(r['今开'])}  最高 {_cell(r['最高'])}  最低 {_cell(r['最低'])}  "
            f"量比 {_cell(r['量比'])}  换手 {_cell(r['换手率'])}%  成交额 {_fmt_amount(r['成交额'])}"
        )
    return lines


# ---------------------------------------------------------------- 工具


@mcp.tool()
@_safe
def search_stock(keyword: str) -> str:
    """按名称或代码模糊搜索 A 股，返回「代码 名称」列表（最多 20 条）。"""
    df, _ = _get_spot_df()
    kw = keyword.strip()
    mask = df["名称"].astype(str).str.contains(kw, na=False) | df["代码"].str.contains(kw, na=False)
    sub = df[mask].drop_duplicates(subset="代码").head(20)
    if sub.empty:
        return f"未找到匹配「{keyword}」的股票"
    return "\n".join(f"{r['代码']}  {r['名称']}" for _, r in sub.iterrows())


@mcp.tool()
@_safe
def get_realtime_quotes(codes: list[str]) -> str:
    """批量查询 A 股实时行情快照：最新价、涨跌幅、今开/最高/最低、成交额、换手率、量比。

    codes 为 6 位股票代码列表，如 ["600519", "000001"]。数据有秒级延迟。
    """
    df, source = _get_spot_df()
    want = {_norm_code(c) for c in codes}
    sub = df[df["代码"].isin(want)]
    if sub.empty:
        return f"未查到这些代码的行情：{sorted(want)}（请先用 search_stock 确认代码）"
    missing = sorted(want - set(sub["代码"]))
    head = f"[数据源:{source}]" + (f" 未覆盖:{','.join(missing)}" if missing else "")
    return head + "\n" + "\n".join(_quote_lines(sub))


def _fetch_kline_em(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    return ak.stock_zh_a_hist_em(symbol=code, period="daily", start_date=start, end_date=end, adjust=adjust)


def _fetch_kline_sina(code: str, start: str, end: str, adjust: str) -> pd.DataFrame:
    prefix = "sh" if code.startswith("6") else ("bj" if code.startswith(("4", "8")) else "sz")
    df = ak.stock_zh_a_hist_sina(symbol=prefix + code, period="daily", start_date=start, end_date=end, adjust=adjust)
    if df.empty:
        return df
    out = df.rename(columns={"date": "日期", "open": "开盘", "close": "收盘", "high": "最高", "low": "最低",
                             "volume": "成交量", "amount": "成交额"})
    out["涨跌幅"] = out["收盘"].pct_change() * 100
    out["日期"] = out["日期"].astype(str)
    return out


@mcp.tool()
@_safe
def get_daily_kline(code: str, days: int = 30, adjust: str = "qfq") -> str:
    """查询个股最近 N 个交易日的日 K 线及区间统计。

    code 为 6 位代码；days 默认 30；adjust: ""=不复权, "qfq"=前复权(默认), "hfq"=后复权。
    返回逐日 OHLC/涨跌幅/成交额 + 区间累计涨跌、最高最低，供形态判断。
    """
    end = datetime.now()
    start = end - timedelta(days=int(days * 2) + 12)
    start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    code = _norm_code(code)
    source = "eastmoney"
    try:
        df = _fetch_kline_em(code, start_s, end_s, adjust)
    except Exception:
        df, source = _fetch_kline_sina(code, start_s, end_s, adjust), "sina"
    df = df.tail(int(days))
    if df.empty:
        return f"{code} 未取到日K数据"
    first, last = float(df["收盘"].iloc[0]), float(df["收盘"].iloc[-1])
    cum = (last / first - 1) * 100 if first else 0.0
    lines = [f"[数据源:{source}]", "日期        开盘   收盘   最高   最低   涨跌%   成交额"]
    for _, r in df.iterrows():
        lines.append(
            f"{r['日期']}  {_cell(r['开盘'])}  {_cell(r['收盘'])}  {_cell(r['最高'])}"
            f"  {_cell(r['最低'])}  {_pct(r['涨跌幅'])}  {_fmt_amount(r['成交额'])}"
        )
    lines.append(
        f"\n区间统计({len(df)}个交易日): 累计涨跌 {cum:+.2f}%  "
        f"区间最高 {_cell(float(df['最高'].max()))}  区间最低 {_cell(float(df['最低'].min()))}"
    )
    return "\n".join(lines)


@mcp.tool()
@_safe
def get_order_book(code: str) -> str:
    """查询个股盘口：卖五到买五档报价挂单、最新价、成交额等。"""
    df = ak.stock_bid_ask_em(symbol=_norm_code(code))
    return "\n".join(f"{r.iloc[0]}: {r.iloc[1]}" for _, r in df.iterrows())


@mcp.tool()
@_safe
def get_market_overview() -> str:
    """查询大盘核心指数快照：上证指数、深证成指、创业板指、沪深300、科创50。"""
    try:
        df = ak.index_zh_spot_em(symbol="沪深重要指数")
        source = "eastmoney"
    except Exception:
        df = ak.index_zh_spot_sina()
        source = "sina"
    name_c = "名称" if "名称" in df.columns else df.columns[1]
    want = {"上证指数", "深证成指", "创业板指", "沪深300", "科创50"}
    sub = df[df[name_c].isin(want)].drop_duplicates(subset=name_c)
    if sub.empty:
        return f"未取到指数数据，接口返回列：{list(df.columns)}"
    price_c = "最新价" if "最新价" in df.columns else df.columns[2]
    pct_c = "涨跌幅" if "涨跌幅" in df.columns else df.columns[3]
    amt_c = "成交额" if "成交额" in df.columns else None
    lines = [f"[数据源:{source}]"]
    for _, r in sub.iterrows():
        amt = f"  成交额 {_fmt_amount(r[amt_c])}" if amt_c else ""
        lines.append(f"{r[name_c]}  {_cell(r[price_c])}  {_pct(r[pct_c])}{amt}")
    return "\n".join(lines)


# ------------------------------------------------------ 自选股（本地文件）


@mcp.tool()
@_safe
def watchlist_add(codes: list[str]) -> str:
    """把股票代码加入本地自选股。"""
    _save_watchlist(_load_watchlist() + [_norm_code(c) for c in codes])
    return f"自选股：{', '.join(_load_watchlist())}"


@mcp.tool()
@_safe
def watchlist_remove(codes: list[str]) -> str:
    """把股票代码移出本地自选股。"""
    drop = {_norm_code(c) for c in codes}
    _save_watchlist([c for c in _load_watchlist() if c not in drop])
    return f"自选股：{', '.join(_load_watchlist()) or '（空）'}"


@mcp.tool()
@_safe
def watchlist_list() -> str:
    """查看本地自选股，并附各股实时行情快照。"""
    wl = _load_watchlist()
    if not wl:
        return "自选股为空，用 watchlist_add 添加，例如 watchlist_add(['600519','000001'])"
    return get_realtime_quotes(wl)


@mcp.tool()
@_safe
def screen_watchlist(direction: str = "", threshold_pct: float = 0.0, sort_by: str = "涨跌幅") -> str:
    """按涨跌幅筛选自选股并排序，用于收盘复盘。

    direction: ""=全部, "up"=涨幅超过 threshold_pct, "down"=跌幅超过 threshold_pct
    （例：跌幅超过3%按成交额排序 → direction="down", threshold_pct=3, sort_by="成交额"）
    sort_by 可选：涨跌幅 / 成交额 / 换手率 / 量比 / 最新价
    """
    wl = _load_watchlist()
    if not wl:
        return "自选股为空，先用 watchlist_add 添加"
    df, source = _get_spot_df()
    sub = df[df["代码"].isin(wl)].copy()
    if direction == "up":
        sub = sub[sub["涨跌幅"] > threshold_pct]
    elif direction == "down":
        sub = sub[sub["涨跌幅"] < -abs(threshold_pct)]
    key = sort_by if sort_by in SPOT_COLS else "涨跌幅"
    sub = sub.sort_values(key, ascending=False, na_position="last")
    if sub.empty:
        return f"自选股中没有符合条件（{direction or '全部'} {threshold_pct}%）的股票"
    out = [f"[数据源:{source}] 筛选: {direction or '全部'} {threshold_pct}%，按 {sort_by} 排序"]
    out.extend(_quote_lines(sub))
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run()
