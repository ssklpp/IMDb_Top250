# 백엔드(api) 이미지. docker-compose.yml에서 쓴다. Railway는 이 파일이 아니라 nixpacks로 빌드한다.
#
# 저장소 루트에 두지 않는다. 루트에 Dockerfile·.dockerignore를 두었더니 Railway 배포가 실패했다
# (nixpacks 대신 이 파일을 쓰거나, .dockerignore가 nixpacks 빌드에도 적용된다). 빌드 컨텍스트는
# 여전히 저장소 루트이고, 제외 목록은 옆의 api.Dockerfile.dockerignore(BuildKit 규칙)가 맡는다.
#
# 버전은 .python-version(3.13)과 맞춘다. 로컬·Railway·CI·컨테이너가 같은 Python을 쓰게 하기 위해서다.

# 1단계: 의존성만 설치한다. requirements.txt가 전체 락이라 코드가 바뀌어도 이 층은 캐시된다.
FROM python:3.13-slim AS deps
RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2단계: 실행 이미지에는 가상환경과 코드만 남긴다.
FROM python:3.13-slim
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY --from=deps /venv /venv
COPY *.py imdb_top250.csv ./

# vectorstore/에 FAISS 인덱스와 대화 DB(checkpoints.sqlite)가 생긴다. compose가 named volume을
# 여기 붙인다. 디렉터리를 미리 만들어 소유자를 정해 두면 새 볼륨이 그 소유자로 초기화된다.
RUN useradd --create-home app && mkdir vectorstore && chown app vectorstore
USER app

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
