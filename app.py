from flask import Flask, jsonify, request
from flask_cors import CORS  # CORS 임포트
import requests
from bs4 import BeautifulSoup
import numpy as np
import random

app = Flask(__name__)
# CORS 설정: 다른 출처에서 오는 요청을 허용 (cross-origin resource sharing)
CORS(app, resources={r"/api/*": {"origins": "*"}}, methods=["GET", "POST", "OPTIONS"])

# 로또 번호 확률 데이터를 가져오는 함수
def fetch_lotto_probability():
    url = "https://dhlottery.co.kr/gameResult.do?method=statByNumber"
    response = requests.get(url)  # 웹 페이지 요청
    response.raise_for_status()  # 요청이 성공했는지 확인
    soup = BeautifulSoup(response.text, 'html.parser')  # HTML 파싱
    
    probability_data = {}  # 번호별 당첨 횟수를 저장할 딕셔너리
    rows = soup.select("table.tbl_data tbody tr")  # 로또 번호 통계 테이블 행 선택
    for row in rows:
        columns = row.find_all("td")  # 각 행에서 td 태그들 찾기
        if len(columns) >= 2:
            number = int(columns[0].get_text(strip=True))  # 번호
            winning_count = float(columns[2].get_text(strip=True))  # 당첨 횟수
            probability_data[number] = int(winning_count)  # 번호와 당첨 횟수 저장
    return probability_data

# 로또 번호 추첨 라운드 계산 함수
def calculate_current_round(probability_data):
    total_winning_count = sum(probability_data.values())  # 전체 당첨 횟수 합산
    current_round = total_winning_count // 7  # 당첨 횟수를 7로 나누어 라운드 계산
    print("현재 로또 회차:", current_round)
    return current_round

# 확률 계산 함수
def calculate_probabilities(probability_data, current_round):
    probabilities = {}
    for number, count in probability_data.items():
        probabilities[number] = (count / current_round) / 7  # 각 번호의 확률 계산
    return probabilities

# 가중치 기반 랜덤 번호 선택 함수
def weighted_random_selection(probabilities, available_numbers, n=6):
    weights = [probabilities[num] for num in available_numbers]  # 번호들의 가중치 리스트
    selected_numbers = np.random.choice(available_numbers, size=n, p=weights/np.sum(weights), replace=False)
    return selected_numbers.tolist()

# 단순 랜덤 번호 선택 함수
def random_selection(available_numbers, n=6):
    return random.sample(available_numbers, n)  # 랜덤으로 n개의 번호 선택

# 가중치 역방향 방식 랜덤 번호 선택 함수
def inverse_weighted_selection(probabilities, available_numbers, n=6):
    inverse_weights = [1/probabilities[num] if probabilities[num] > 0 else 0 for num in available_numbers]  # 역방향 가중치 계산
    selected_numbers = np.random.choice(available_numbers, size=n, p=inverse_weights/np.sum(inverse_weights), replace=False)
    return selected_numbers.tolist()

# 번호군 그룹을 바탕으로 번호를 선택하는 함수
def select_numbers_from_groups(selected_groups, probabilities,method_choice, n=6):
    group_ranges = {
        '[1, 10]': list(range(1, 11)),
        '[11, 20]': list(range(11, 21)),
        '[21, 30]': list(range(21, 31)),
        '[31, 40]': list(range(31, 41)),
        '[41, 45]': list(range(41, 46))
    }

    available_numbers = []  # 사용할 수 있는 번호들
    for group in selected_groups:
        group_str = f"[{group[0]}, {group[1]}]"  # 그룹을 문자열로 변환
        if group_str in group_ranges:
            available_numbers.extend(group_ranges[group_str])  # 문자열로 매칭해서 번호 추가
        else:
            print(f"그룹 {group_str}이 group_ranges에 없습니다.")  # 디버깅을 위한 출력
    print("가능한 번호(available_numbers):", available_numbers)

    mandatory_numbers = []  # 반드시 뽑을 번호들
    for group in selected_groups:
        group_str = f"[{group[0]}, {group[1]}]"  # 그룹을 문자열로 변환
        current_group_numbers = group_ranges.get(group_str, [])  # 그룹 내 번호들
        print(f"{group_str} 그룹 번호:", current_group_numbers)
        
        # 추출 방법에 따라 번호 선택
        if method_choice == 1:
            mandatory_numbers.append(weighted_random_selection(probabilities, current_group_numbers, 1)[0])
        elif method_choice == 2:
            mandatory_numbers.append(random_selection(current_group_numbers, 1)[0])
        else:
            mandatory_numbers.append(inverse_weighted_selection(probabilities, current_group_numbers, 1)[0])
    print("필수 포함 번호(mandatory_numbers):", mandatory_numbers)

    # 선택된 번호가 총 6개가 될 때까지 추가
    chosen_numbers = mandatory_numbers.copy()
    print("초기 선택된 번호(chosen_numbers):", chosen_numbers)

    while len(chosen_numbers) < n:  # 6개 이상이 되면 종료
        remaining_numbers = list(set(available_numbers) - set(chosen_numbers))  # 선택되지 않은 번호들
        print("남은 번호(remaining_numbers):", remaining_numbers)

        if not remaining_numbers:
            print("남은 번호가 없습니다.")
            break

        print(f"추출 방법(method_choice): {method_choice}")

        # 나머지 번호들 중에서 추출 방법에 맞게 번호를 선택하여 추가
        if method_choice == 1:
            new_numbers = weighted_random_selection(probabilities, remaining_numbers, n - len(chosen_numbers))  # 7로 변경
        elif method_choice == 2:
            new_numbers = random_selection(remaining_numbers, n - len(chosen_numbers))  # 7로 변경
        else:
            new_numbers = inverse_weighted_selection(probabilities, remaining_numbers, n - len(chosen_numbers))  # 7로 변경

        # 새로운 번호 추가
        chosen_numbers.extend(new_numbers)
        print("현재 선택된 번호(chosen_numbers):", chosen_numbers)

        # 번호가 이미 6개 이상 선택되었는지 확인
        if len(chosen_numbers) >= n:
            print("선택된 번호가 6개 이상이 되었습니다.")
        else:
            print("아직 6개 미만입니다.")

    # 중복 제거 후 번호 반환
    return list(set(chosen_numbers))  

# API 엔드포인트 추가
@app.route('/api/numbers', methods=['POST', 'OPTIONS'])
def get_numbers():
    if request.method == 'OPTIONS':  # OPTIONS 요청에 대한 처리
        return '', 200
    
    # 클라이언트에서 받은 데이터 (번호군 및 추출 방법)
    data = request.get_json()
    selected_groups = data.get('selected_groups', [])  # 선택된 번호군
    method = data.get('method', 1)  # 선택된 추출 방법 (기본값은 1)
    method_choice = method  # 이제 이 값을 이용하여 선택된 방법을 처리
    print("선택된 method_choice:", method_choice)

    # 확률 데이터 가져오기
    probability_data = fetch_lotto_probability()
    current_round = calculate_current_round(probability_data)  # 현재 라운드 계산
    probabilities = calculate_probabilities(probability_data, current_round)  # 번호 확률 계산

    # 선택된 추출 방법에 따라 번호 추출
    if method == 1:
        print("Method 1 선택: select_numbers_from_groups 실행")
        print("selected_groups:", selected_groups)
        print("probabilities:", probabilities)
        numbers = select_numbers_from_groups(selected_groups, probabilities,method , n=6)  # 그룹에서 번호 추출
        print("추출된 번호:", numbers)
    elif method == 2:
        print("Method 2 선택: random_selection 실행")
        print("selected_groups:", selected_groups)
        numbers = select_numbers_from_groups(selected_groups, probabilities, method, n=6) 
        print("추출된 번호:", numbers)
    else:
        print("Method 3 선택: select_numbers_from_groups 실행")
        print("selected_groups:", selected_groups)
        print("probabilities :", probabilities)
        numbers = select_numbers_from_groups(selected_groups, probabilities,method, n=6)
        print("추출된 번호: " , numbers)

    # 번호를 오름차순으로 정렬하여 반환
    numbers.sort()
    print("서버에서 반환하는 번호:", numbers)  # 콘솔에 번호 출력
    return jsonify({"numbers": numbers})  # 클라이언트로 번호 반환

# 서버 실행
if __name__ == '__main__':
    app.run(debug=True)