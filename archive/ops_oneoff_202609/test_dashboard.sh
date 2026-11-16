#!/bin/bash
cd /d/zcodesj
taskkill /pid 12473 /f 2>/dev/null
sleep 2
python -m uvicorn tulong_dashboard.app:app --host 0.0.0.0 --port 3019 > /d/zcodesj/tsp_dashboard.log 2>&1 &
sleep 10

echo "=== overview ==="
curl -s http://localhost:3019/api/overview
echo ""

echo "=== seal ==="
curl -s http://localhost:3019/api/seal | python -c "import sys,json; d=json.load(sys.stdin); print(f'date={d[\"date\"]} sealed={d[\"sealed\"]} broken={d[\"broken\"]} seal_rate={d[\"seal_rate\"]}%'); [print(f'  {x[\"code\"]} {x[\"name\"]} {x[\"amount_yi\"]}yi') for x in d['top10'][:3]]"
echo ""

echo "=== emotion ==="
curl -s http://localhost:3019/api/emotion | python -c "import sys,json; d=json.load(sys.stdin); print(f'score={d[\"score\"]} label={d[\"label\"]}'); [print(f'  {k}={v}') for k,v in d['radar'].items()]"
echo ""

echo "=== rps concept ==="
curl -s http://localhost:3019/api/rps/concept | python -c "import sys,json; d=json.load(sys.stdin);[print(f'  {x[\"dim\"]} {x[\"avg_pct\"]:.1f}%') for x in d['today_top'][:5]]"
echo ""

echo "=== positions ==="
curl -s http://localhost:3019/api/positions | python -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"total\"]}pos pnl={d[\"pnl\"]/10000:.2f}w')"
echo ""

echo "=== events & home ==="
curl -s http://localhost:3019/api/events
echo ""
curl -s -o /dev/null -w "首页:%{http_code}" http://localhost:3019/
echo ""

grep -i -c "error\|traceback\|500" /d/zcodesj/tsp_dashboard.log 2>/dev/null && echo "WARN: errors in log" || echo "0 errors in log"