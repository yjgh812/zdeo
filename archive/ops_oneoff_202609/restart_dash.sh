taskkill //PID 24636 //F
sleep 3
cd /d/zcodesj
nohup python -m uvicorn tulong_dashboard.app:app --host 0.0.0.0 --port 3019 > tsp_dashboard.log 2>&1 &
sleep 15
echo "--- testing ---"
python -c "import urllib.request,json;d=json.loads(urllib.request.urlopen('http://localhost:3019/api/emotion').read());print('emotion:',d['score'],d['label'],d.get('error',''))"
python -c "import urllib.request,json;d=json.loads(urllib.request.urlopen('http://localhost:3019/api/overview').read());print('overview:',d['sealed'],d['seal_rate'])"
python -c "import urllib.request;r=urllib.request.urlopen('http://localhost:3019/');print('home:',r.status)"
python -c "import urllib.request,json;d=json.loads(urllib.request.urlopen('http://localhost:3019/api/rps/industry').read());print('rps:',[x['dim'] for x in d['today_top'][:3]])"
echo "--- errors ---"
grep -c "Traceback\|ERROR\|500" /d/zcodesj/tsp_dashboard.log || echo "0"