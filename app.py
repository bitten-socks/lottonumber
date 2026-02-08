from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import numpy as np
import random
import re
import urllib3
import json
import os
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta

# SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

HISTORICAL_FILE = "historical_data.json"

# 네이버 크롤링용 헤더
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ---------------------------------------------------------------------------
# 1. 유틸리티 & 데이터 관리
# ---------------------------------------------------------------------------

def calculate_expected_round():
    """
    오늘 날짜를 기준으로 추첨되었어야 할 최신 회차를 계산합니다.
    기준: 1회차 (2002-12-07 20:40)
    """
    base_date = datetime(2002, 12, 7, 20, 40)
    now = datetime.now()
    
    diff = now - base_date
    days = diff.days
    
    # 1주일 = 7일
    rounds = days // 7 + 1
    return rounds

def load_historical_data():
    if not os.path.exists(HISTORICAL_FILE):
        return []
    try:
        with open(HISTORICAL_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_historical_data(data):
    try:
        # 회차순 정렬 (오름차순)
        data.sort(key=lambda x: x['round'])
        # 중복 제거 (회차 기준)
        unique_data = {d['round']: d for d in data}.values()
        final_list = sorted(list(unique_data), key=lambda x: x['round'])
        
        with open(HISTORICAL_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, indent=4, ensure_ascii=False)
        print("[System] 데이터 파일 저장 완료.")
    except Exception as e:
        print(f"[ERROR] 데이터 저장 실패: {e}")

# ---------------------------------------------------------------------------
# 2. 네이버 크롤링 함수 (파싱 강화)
# ---------------------------------------------------------------------------

def fetch_lotto_from_naver(round_no=None):
    """
    네이버 검색을 통해 로또 데이터를 가져옵니다.
    """
    query = "로또당첨번호"
    if round_no:
        query = f"{round_no}회 로또당첨번호"
        
    url = f"https://search.naver.com/search.naver?where=nexearch&sm=top_hty&fbm=0&ie=utf8&query={query}"
    print(f"[Crawler] 네이버 접속 시도: {query}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        win_ball_div = soup.select_one('.win_ball')
        bonus_ball_div = soup.select_one('.bonus_ball')

        if not win_ball_div:
             win_ball_div = soup.select_one('.num_box')

        if not win_ball_div:
            print("[Crawler] 번호 영역(.win_ball 등)을 찾지 못함")
            return None

        # [수정] 회차 추출 강화
        fetched_round = 0
        
        # 여러 클래스 시도 (.lottery_wrap, .n_lotto, .cs_lotto)
        lotto_wrap = soup.select_one('.lottery_wrap') or soup.select_one('.n_lotto') or soup.select_one('.cs_lotto')
        
        if lotto_wrap:
             title_text = lotto_wrap.get_text()
             # "1,161회" 또는 "1161회" 패턴 찾기
             round_match = re.search(r'(\d{3,4}),?(\d*)회', title_text)
             if round_match:
                 # 콤마 제거 후 정수 변환
                 raw_str = round_match.group(1) + round_match.group(2)
                 fetched_round = int(raw_str)
             
             # [추가] 파싱 실패 시, 텍스트 내에 요청한 회차가 포함되어 있는지 확인 (Fallback)
             if fetched_round == 0 and round_no:
                 if f"{round_no}회" in title_text:
                     print(f"[Crawler] 회차 파싱 실패했으나 텍스트에 '{round_no}회' 포함됨. 올바른 결과로 간주.")
                     fetched_round = int(round_no)
        
        # 회차 검증
        if round_no and fetched_round != int(round_no):
            print(f"[Crawler] 요청 회차({round_no})와 검색 결과({fetched_round}) 불일치")
            return None

        # 번호 추출
        win_nums = []
        spans = win_ball_div.select('span.ball')
        for span in spans:
            txt = span.get_text(strip=True)
            if txt.isdigit():
                win_nums.append(int(txt))
        
        bonus_num = 0
        if bonus_ball_div:
             bonus_span = bonus_ball_div.select_one('span.ball')
             if bonus_span:
                 bonus_num = int(bonus_span.get_text(strip=True))
        
        # 보너스 보정 (한 div안에 다 있는 경우)
        if len(win_nums) >= 7 and bonus_num == 0:
             bonus_num = win_nums.pop()

        if len(win_nums) == 6 and bonus_num > 0:
            # 회차 정보가 0이어도 번호가 확실하고 요청한 회차가 있다면 그걸로 간주
            if fetched_round == 0 and round_no:
                fetched_round = int(round_no)
                
            print(f"[Crawler] {fetched_round}회 데이터 확보 성공")
            return {
                "round": fetched_round,
                "winning_numbers": sorted(win_nums),
                "bonus": bonus_num
            }
            
    except Exception as e:
        print(f"[ERROR] 크롤링 실패: {e}")
        
    return None

def ensure_latest_data():
    """
    최신 데이터가 있는지 확인하고, 없으면 업데이트합니다.
    """
    history = load_historical_data()
    
    last_saved = 0
    if history:
        last_saved = max(d['round'] for d in history)
        
    expected = calculate_expected_round()
    
    if expected > last_saved:
        print(f"[Update] 최신 데이터 업데이트 필요 (저장됨: {last_saved}회 / 예상: {expected}회)")
        
        updated_count = 0
        # 누락된 회차 순차적으로 가져오기
        for r in range(last_saved + 1, expected + 1):
            data = fetch_lotto_from_naver(r)
            if data:
                history.append(data)
                updated_count += 1
                time.sleep(1.5) # 차단 방지 딜레이
            else:
                print(f"[Update] {r}회 데이터 가져오기 실패 (아직 미발표일 수 있음)")
                break 
        
        if updated_count > 0:
            save_historical_data(history)
    else:
        print(f"[System] 데이터 최신 상태입니다. (최신: {last_saved}회)")
            
    return history

# ---------------------------------------------------------------------------
# 3. 비즈니스 로직
# ---------------------------------------------------------------------------

def get_stats(history):
    stats = {i: 0 for i in range(1, 46)}
    if not history: return {i: 1 for i in range(1, 46)}
    for game in history:
        for num in game['winning_numbers']:
            if 1 <= num <= 45: stats[num] += 1
        if 'bonus' in game:
            b = game['bonus']
            if 1 <= b <= 45: stats[b] += 1
    return stats

def parse_lotto_qr(qr_url):
    try:
        parsed_url = urlparse(qr_url)
        qs = parse_qs(parsed_url.query)
        v_param = qs.get('v', [''])[0]
        if not v_param: return None
        parts = re.split(r'[qm]', v_param)
        if not parts: return None
        round_no = int(parts[0])
        games = []
        for part in parts[1:]:
            clean_part = re.sub(r'\D', '', part)
            if len(clean_part) >= 12:
                nums_str = clean_part[:12]
                nums = [int(nums_str[i:i+2]) for i in range(0, 12, 2)]
                games.append(sorted(nums))
        return {'round': round_no, 'games': games}
    except: return None

def calculate_rank(my_nums, win_nums, bonus_num):
    cnt = len(set(my_nums) & set(win_nums))
    if cnt == 6: return "1등"
    elif cnt == 5 and bonus_num in my_nums: return "2등"
    elif cnt == 5: return "3등"
    elif cnt == 4: return "4등"
    elif cnt == 3: return "5등"
    else: return "낙첨"

# ---------------------------------------------------------------------------
# 4. API Routes
# ---------------------------------------------------------------------------

@app.route('/api/numbers', methods=['POST'])
def generate_numbers():
    data = request.get_json()
    mode = data.get('method', 1) 
    selected_groups = data.get('selected_groups', [])
    
    history = load_historical_data()
    stats = get_stats(history)
    
    pool = []
    if not selected_groups:
        pool = list(range(1, 46))
    else:
        for group in selected_groups:
            start, end = group
            pool.extend(range(start, end + 1))
    pool = sorted(list(set(pool)))
    
    if len(pool) < 6: return jsonify({"error": "Not enough numbers"}), 400

    weights = []
    for num in pool:
        w = stats.get(num, 1)
        if w == 0: w = 1
        if mode == 3: w = 1 / w 
        elif mode == 2: w = 1
        weights.append(w)
    
    total_w = sum(weights)
    probs = [w / total_w for w in weights]
    selected = np.random.choice(pool, size=6, replace=False, p=probs)
    return jsonify({"numbers": sorted(selected.tolist())})

@app.route('/api/register-lotto', methods=['POST'])
def check_qr_result():
    data = request.get_json()
    qr_url = data.get('url')
    
    parsed = parse_lotto_qr(qr_url)
    if not parsed: return jsonify({"error": "Invalid QR"}), 400
        
    round_no = parsed['round']
    my_games = parsed['games']
    
    history = load_historical_data()
    win_info = next((item for item in history if item["round"] == round_no), None)
    
    if not win_info:
        print(f"[Check] {round_no}회 데이터 로컬에 없음. 네이버 검색 시도...")
        new_data = fetch_lotto_from_naver(round_no)
        if new_data:
            win_info = new_data
            history.append(new_data)
            save_historical_data(history)
    
    if not win_info:
        return jsonify({
            "round": round_no,
            "status": "pending",
            "registeredNumbers": [], 
            "bonus": 0,
            "rowData": [{'row': chr(65+i), 'numbers': game, 'result': '추첨전'} for i, game in enumerate(my_games)]
        })

    win_nums = win_info['winning_numbers']
    bonus = win_info['bonus']
    row_data = []
    
    for i, game in enumerate(my_games):
        rank = calculate_rank(game, win_nums, bonus)
        row_data.append({
            'row': chr(65+i),
            'numbers': game,
            'result': rank
        })
        
    return jsonify({
        "round": round_no,
        "status": "completed",
        "registeredNumbers": win_nums,
        "bonus": bonus,
        "rowData": row_data
    })

@app.route('/api/numbers/recommend', methods=['GET'])
def recommend_numbers():
    recommendations = [[1, 1, 2, 2, 3, 4], [1, 2, 3, 4, 4, 5], [2, 2, 3, 3, 4, 5]]
    return jsonify({"recommended_numbers": recommendations})

if __name__ == '__main__':
    # [수정] 이중 실행 방지: WERKZEUG_RUN_MAIN 환경변수 확인
    # Flask 디버거가 활성화되면 메인 프로세스가 자식 프로세스를 생성하므로 코드가 두 번 실행됨
    # 실제 로직은 자식 프로세스(RUN_MAIN='true')에서만 돌도록 설정
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print(">>> 서버 시작 (Main Process) <<<")
        ensure_latest_data()
    
    app.run(debug=True, port=5000)