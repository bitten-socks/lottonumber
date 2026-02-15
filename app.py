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
# 2. 네이버 크롤링 함수 (파싱 강화 - 선택자 추가)
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
        
        # [수정 1] 다양한 당첨 번호 컨테이너 선택자 시도
        # .win_ball (구버전), .num_box, .win_number_box, .winning_number 등
        win_ball_div = (
            soup.select_one('.win_ball') or 
            soup.select_one('.num_box') or 
            soup.select_one('.win_number_box') or
            soup.select_one('.winning_number') or
            soup.select_one('.lotto_win_number') # 혹시 모를 클래스명
        )
        
        bonus_ball_div = (
            soup.select_one('.bonus_ball') or
            soup.select_one('.bonus_number')
        )

        if not win_ball_div:
            # 컨테이너를 못 찾았을 경우, 전체 구조에서 'ball' 클래스나 숫자 형태를 직접 찾기 시도
            # (최후의 수단: 특정 영역 안의 span들을 긁어옴)
            print("[Crawler] 주요 컨테이너(.win_ball 등) 찾기 실패. 대체 탐색 시도...")
            lotto_wrap = soup.select_one('.lottery_wrap') or soup.select_one('.n_lotto')
            if lotto_wrap:
                # 랩퍼 안의 모든 숫자 span을 찾음
                pass # 아래 로직에서 처리
            else:
                print("[Crawler] 번호 영역을 찾지 못함")
                return None

        # [수정 2] 회차 추출 강화
        fetched_round = 0
        lotto_wrap = soup.select_one('.lottery_wrap') or soup.select_one('.n_lotto') or soup.select_one('.cs_lotto') or soup.select_one('.lottery_grp')
        
        if lotto_wrap:
             title_text = lotto_wrap.get_text()
             # "1,161회" 또는 "1161회" 패턴 찾기
             round_match = re.search(r'(\d{3,4}),?(\d*)회', title_text)
             if round_match:
                 # 콤마 제거 후 정수 변환
                 raw_str = round_match.group(1) + round_match.group(2)
                 fetched_round = int(raw_str)
             
             if fetched_round == 0 and round_no:
                 if f"{round_no}회" in title_text:
                     print(f"[Crawler] 회차 파싱 실패했으나 텍스트에 '{round_no}회' 포함됨. 올바른 결과로 간주.")
                     fetched_round = int(round_no)
        
        # 회차 검증
        if round_no and fetched_round != 0 and fetched_round != int(round_no):
            print(f"[Crawler] 요청 회차({round_no})와 검색 결과({fetched_round}) 불일치. (최신 회차가 아직 업데이트 안 되었을 수 있음)")
            return None

        # [수정 3] 번호 추출 로직 유연화
        win_nums = []
        
        # 방법 A: win_ball_div 안의 span.ball 찾기
        if win_ball_div:
            spans = win_ball_div.select('span.ball') or win_ball_div.select('span')
            for span in spans:
                txt = span.get_text(strip=True)
                if txt.isdigit():
                    win_nums.append(int(txt))
        
        # 방법 B: 만약 위에서 못 찾았다면 lotto_wrap 전체에서 찾기 (보너스 포함될 수 있음)
        if len(win_nums) < 6 and lotto_wrap:
            spans = lotto_wrap.select('span.ball')
            temp_nums = []
            for span in spans:
                txt = span.get_text(strip=True)
                if txt.isdigit():
                    temp_nums.append(int(txt))
            # 보통 6개+1개(보너스) 혹은 6개만 나옴
            if len(temp_nums) >= 6:
                win_nums = temp_nums

        # 보너스 번호 추출
        bonus_num = 0
        if bonus_ball_div:
             bonus_span = bonus_ball_div.select_one('span.ball') or bonus_ball_div.select_one('span')
             if bonus_span:
                 txt = bonus_span.get_text(strip=True)
                 if txt.isdigit():
                    bonus_num = int(txt)
        
        # 보너스 보정 (한 리스트 안에 다 있는 경우 분리)
        # 예: [1, 2, 3, 4, 5, 6, 7] -> 7이 보너스
        if len(win_nums) >= 7 and bonus_num == 0:
             bonus_num = win_nums.pop()
        elif len(win_nums) == 7 and bonus_num != 0:
             # 이미 보너스를 찾았는데 win_nums에도 7개가 있다면 마지막꺼 제거 (중복 가능성)
             if win_nums[-1] == bonus_num:
                 win_nums.pop()

        if len(win_nums) == 6 and bonus_num > 0:
            # 회차 정보가 0이어도 번호가 확실하고 요청한 회차가 있다면 그걸로 간주
            if fetched_round == 0 and round_no:
                fetched_round = int(round_no)
            
            # 최종 확인: 요청한 회차가 0(자동 최신)이거나, 추출된 회차와 같거나, 추출실패(0)시
            if not round_no or (fetched_round == int(round_no)) or fetched_round == 0:
                print(f"[Crawler] {fetched_round if fetched_round else '최신'}회 데이터 확보 성공: {win_nums} + {bonus_num}")
                return {
                    "round": fetched_round if fetched_round else int(round_no if round_no else 0),
                    "winning_numbers": sorted(win_nums),
                    "bonus": bonus_num
                }
            else:
                print("[Crawler] 번호 추출 성공했으나 회차 불일치")

        else:
            print(f"[Crawler] 번호 추출 실패. 추출된 개수: {len(win_nums)}, 보너스: {bonus_num}")
            
    except Exception as e:
        print(f"[ERROR] 크롤링 실패: {e}")
        import traceback
        traceback.print_exc()
        
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
    
    # [안전장치] 만약 토요일 오후 8시 40분 이전이라면 아직 추첨 전이므로 expected를 1 줄임
    # 하지만 calculate_expected_round 로직상 날짜 기준이라 하루 정도 오차는 괜찮음
    # (일요일에 실행하면 문제 없음)
    
    if expected > last_saved:
        print(f"[Update] 최신 데이터 업데이트 필요 (저장됨: {last_saved}회 / 예상: {expected}회)")
        
        updated_count = 0
        # 누락된 회차 순차적으로 가져오기
        for r in range(last_saved + 1, expected + 1):
            data = fetch_lotto_from_naver(r)
            if data:
                # 중복 방지 (이미 있는지 확인)
                if not any(d['round'] == data['round'] for d in history):
                    history.append(data)
                    updated_count += 1
                    print(f"[Update] {data['round']}회 추가 완료")
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
    
    # 로컬에 없으면 크롤링 시도
    if not win_info:
        print(f"[Check] {round_no}회 데이터 로컬에 없음. 네이버 검색 시도...")
        new_data = fetch_lotto_from_naver(round_no)
        if new_data:
            win_info = new_data
            # 중복 방지 후 저장
            if not any(d['round'] == new_data['round'] for d in history):
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
    # 이중 실행 방지: WERKZEUG_RUN_MAIN 환경변수 확인
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print(">>> 서버 시작 (Main Process) <<<")
        ensure_latest_data()
    
    app.run(debug=True, port=5000)