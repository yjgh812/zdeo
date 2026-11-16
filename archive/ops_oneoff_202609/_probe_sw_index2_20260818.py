# -*- coding: utf-8 -*-
import akshare as ak
df = ak.index_hist_sw(symbol="801011", period="day")
print("L2(801011 林业II) 行数:", len(df), "列:", list(df.columns))
print(df.tail(2).to_string())
print()
df2 = ak.index_hist_sw(symbol="801010", period="day")
print("L1(801010) 尾部 列:", list(df2.columns))
print(df2.tail(2).to_string())
