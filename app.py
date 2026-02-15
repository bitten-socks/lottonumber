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
import traceback

# SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

HISTORICAL_FILE = "historical_data.json"

# 네이버 크롤링용 헤더 (브라우저인 척 위장)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://www.naver.com/'
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
# 2. 네이버 크롤링 함수 (파싱 강화 - 선택자 대폭 추가)
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
        
        # [수정 1] 당첨 번호를 감싸는 컨테이너 후보군 (PC, 모바일, 구버전 대응)
        candidates = [
            '.win_number_box',  # 최신 PC 구조 (스크린샷 기반)
            '.win_ball',        # 구버전 또는 내부 래퍼
            '.num_box',         # 모바일 또는 다른 뷰
            '.winning_number',  # 번호 직접 감싸는 태그
            '.lotto_win_number',
            '.win_result'       # 모바일 뷰
        ]
        
        win_container = None
        for selector in candidates:
            found = soup.select_one(selector)
            if found:
                win_container = found
                # print(f"[Crawler] 컨테이너 발견: {selector}") # 디버깅용
                break
        
        # 컨테이너를 못 찾았으면 전체 래퍼라도 잡아서 시도
        if not win_container:
            win_container = soup.select_one('.lottery_wrap') or soup.select_one('.n_lotto') or soup.select_one('.cs_lotto')

        if not win_container:
            print("[Crawler] HTML 구조 분석 실패 (번호 영역을 찾지 못함)")
            return None

        # [수정 2] 회차 정보 추출 (유연하게)
        fetched_round = 0
        title_area = soup.select_one('.lottery_wrap') or soup.select_one('.n_lotto') or soup.select_one('.cs_lotto') or soup
        
        if title_area:
             title_text = title_area.get_text()
             # "1,211회", "1211회" 패턴 찾기
             round_match = re.search(r'(\d{1,4}),?(\d{0,3})회', title_text)
             if round_match:
                 raw_str = round_match.group(1) + round_match.group(2)
                 fetched_round = int(raw_str)
             
             # 파싱 실패했으나 텍스트에 요청 회차가 있다면 그걸로 간주 (Fallback)
             if fetched_round == 0 and round_no:
                 if f"{round_no}회" in title_text:
                     print(f"[Crawler] 회차 파싱 실패했으나 텍스트에 '{round_no}회' 확인됨.")
                     fetched_round = int(round_no)
        
        # [중요] 회차 검증 로직 완화
        # 검색 결과가 없거나(0), 요청한 회차와 다르면 문제. 
        # 단, fetched_round가 0이어도 번호를 찾았다면 일단 진행.
        if round_no and fetched_round != 0 and fetched_round != int(round_no):
            print(f"[Crawler] 요청 회차({round_no})와 검색 결과({fetched_round}) 불일치. (최신 회차가 아직 업데이트 안 되었을 수 있음)")
            return None

        # [수정 3] 번호 추출 (모든 span.ball 긁어오기 전략)
        # 특정 클래스(.winning_number 등)에 얽매이지 않고, 숫자 볼 형태를 모두 수집
        all_balls = win_container.select('span.ball')
        
        # 만약 span.ball이 없으면 그냥 span만 찾아서 숫자 필터링
        if not all_balls:
            all_balls = win_container.select('span')
            
        extracted_nums = []
        for ball in all_balls:
            txt = ball.get_text(strip=True)
            if txt.isdigit():
                num = int(txt)
                if 1 <= num <= 45: # 로또 번호 범위 체크
                    extracted_nums.append(num)
        
        # 중복 제거 없이 순서대로 수집 (보통 당첨번호 6개 + 보너스 1개 순서로 나옴)
        # 네이버 구조상 당첨번호 6개는 앞에서 나오고 보너스는 뒤에 나옴
        
        if len(extracted_nums) >= 7:
            # 보통 7개 이상 나오면 앞 6개가 당첨, 마지막이나 특정 위치가 보너스
            # 중복된 숫자가 나올 수도 있으므로 (HTML 구조상) 유니크하게 처리하지 않고 흐름대로
            
            # 마지막 숫자를 보너스로 간주 (네이버 UI상 맨 뒤에 보너스 위치)
            bonus_num = extracted_nums[-1]
            win_nums = extracted_nums[:6]
            
            # 검증: 번호가 6개인지
            if len(set(win_nums)) < 6:
                # 중복 등으로 개수가 모자라면 다시 정제
                win_nums = list(dict.fromkeys(extracted_nums))[:6] # 순서 유지 중복 제거
                if len(extracted_nums) > 6:
                    bonus_num = extracted_nums[-1]

            # 최종 회차 결정 (파싱 실패시 요청 회차 사용)
            final_round = fetched_round if fetched_round != 0 else int(round_no if round_no else 0)
            
            if final_round == 0:
                print("[Crawler] 번호는 찾았으나 회차 정보를 확정할 수 없음.")
                # 최신 회차를 요청한 경우가 아니면 저장하지 않음 (오류 방지)
                if not round_no:
                    # 최신 회차 계산해서 할당
                    final_round = calculate_expected_round()
                    # 만약 토요일 저녁이라 아직 안나왔을 수도 있으니 주의. 
                    # 하지만 데이터가 있다는건 나왔다는 뜻.

            print(f"[Crawler] {final_round}회 데이터 확보 성공: {win_nums} + {bonus_num}")
            return {
                "round": final_round,
                "winning_numbers": sorted(list(set(win_nums))), # 정렬 및 중복 확실히 제거
                "bonus": bonus_num
            }

        else:
            print(f"[Crawler] 번호 개수 부족. 추출된 숫자: {extracted_nums}")
            return None
            
    except Exception as e:
        print(f"[ERROR] 크롤링 실패: {e}")
        traceback.print_exc() # 상세 에러 로그 출력
        
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
                # 중복 방지
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