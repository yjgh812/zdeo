import sys
sys.path.insert(0, "D:/zcodesj")
from tulong_dashboard.app import _pct_distribution
d = _pct_distribution()
print("pcts:", len(d.get("pcts", [])), "err:", d.get("error", "none"))