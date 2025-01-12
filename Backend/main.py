from fastapi import FastAPI, HTTPException, Depends
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from DB import models, schemas, crud
from DB.database import engine, SessionLocal
from DB.models import Base
from dotenv import load_dotenv
import uvicorn
import requests
from secrets import token_hex
import os
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from Middleware.mid import TimingMiddleware, RateLimitMiddleware
import time
from collections import defaultdict

load_dotenv()

app = FastAPI()

# Add session middleware
app.add_middleware(SessionMiddleware, secret_key=token_hex(32))

# CORS configuration (adjust as needed for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Security risk in production
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Performance and monitoring middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TimingMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

# url/docs 로 기능 설명 페이지 안내
@app.get("/")
async def hello():
    return {"hello": "/docs for more info"}

Base.metadata.create_all(bind=engine)

# -------------------
# 데이터베이스 의존성
# -------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------
# 유저 정보 CRUD
# -------------------
# Create (POST) - 사용자 생성
@app.post("/users/", response_model=schemas.UserResponse)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_user = crud.get_user_by_email(db=db, user_email=user.userEmail)
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")
    
    return crud.create_user(db=db, user=user)

# Read (GET all) - 모든 사용자 조회
@app.get("/users/", response_model=list[schemas.UserResponse])
def read_users(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_users(db=db, skip=skip, limit=limit)

# Read (GET by email) - 특정 사용자 조회
@app.get("/users/{user_email}", response_model=schemas.UserResponse)
def read_user(user_email: str, db: Session = Depends(get_db)):
    user = crud.get_user_by_email(db=db, user_email=user_email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user

# Update (PUT) - 사용자 정보 수정
@app.put("/users/{user_email}", response_model=schemas.UserResponse)
def update_user(user_email: str, updates: schemas.UserUpdate, db: Session = Depends(get_db)):
    updated_user = crud.update_user(db=db, user_email=user_email, updates=updates)
    
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return updated_user

# Delete (DELETE) - 사용자 삭제
@app.delete("/users/{user_email}")
def delete_user(user_email: str, db: Session = Depends(get_db)):
    result = crud.delete_user(db=db, user_email=user_email)
    
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"message": "User deleted successfully"}

# -------------------
# 구글 독스 파일 CRUD
# -------------------
# Create (POST) - 파일 생성
@app.post("/files/", response_model=schemas.FileResponse)
def create_file(file: schemas.FileCreate, db: Session = Depends(get_db)):
    return crud.create_file(db=db, file=file)

# Read (GET all) - 모든 파일 조회
@app.get("/files/", response_model=list[schemas.FileResponse])
def read_files(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_files(db=db, skip=skip, limit=limit)

# Read (GET by ID) - 특정 파일 조회
@app.get("/files/{file_id}", response_model=schemas.FileResponse)
def read_file(file_id: int, db: Session = Depends(get_db)):
    file_record = crud.get_file_by_id(db=db, file_id=file_id)
    
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")
    
    return file_record

# Update (PUT) - 파일 정보 수정
@app.put("/files/{file_id}", response_model=schemas.FileResponse)
def update_file(file_id: int, updates: schemas.FileUpdate, db: Session = Depends(get_db)):
    updated_file = crud.update_file(db=db, file_id=file_id, updates=updates)
    
    if not updated_file:
        raise HTTPException(status_code=404, detail="File not found")
    
    return updated_file

# Delete (DELETE) - 파일 삭제
@app.delete("/files/{file_id}")
def delete_file(file_id: int, db: Session = Depends(get_db)):
    success = crud.delete_file(db=db, file_id=file_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="File not found")
    
    return {"message": "File deleted successfully"}

# -------------------
# RunPod 통신 함수
# -------------------
def send_to_runpod(question: str) -> str:
    url = os.getenv("RUNPOD_URL")
    headers = {"Authorization": "Bearer " + os.getenv("RUNPOD_API_KEY")}
    body = {
        "input": {
            "api": {
                "method": "POST",
                "endpoint": "/generate",  # RunPod의 LLM 엔드포인트
            },
            "payload": {"question": question},
        }
    }
    response = requests.post(url, json=body, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json().get("output", {}).get("answer", "No answer received")

# -------------------
# 질문 기록 CRUD 및 RunPod 연동
# -------------------

# Create (POST) - 질문 생성 및 RunPod 호출
@app.post("/qnas/", response_model=schemas.QnaResponse)
def create_qna(qna: schemas.QnaCreate, db: Session = Depends(get_db)):
    # 질문 저장 (답변은 None 상태로 저장)
    db_qna = crud.create_qna(db=db, qna=qna)

    # RunPod에 질문 전송 및 답변 수신
    try:
        answer = send_to_runpod(qna.question)
        db_qna.answer = answer  # 답변 업데이트
        db.commit()
        db.refresh(db_qna)
        return db_qna
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Read (GET all) - 모든 QnA 조회
@app.get("/qnas/", response_model=list[schemas.QnaResponse])
def read_qnas(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_qnas(db=db, skip=skip, limit=limit)

# Read (GET by ID) - 특정 QnA 조회
@app.get("/qnas/{qna_id}", response_model=schemas.QnaResponse)
def read_qna(qna_id: int, db: Session = Depends(get_db)):
    qna_record = crud.get_qna_by_id(db=db, qna_id=qna_id)
    
    if not qna_record:
        raise HTTPException(status_code=404, detail="QnA not found")
    
    return qna_record

# Update (PUT) - QnA 정보 수정 및 RunPod 재호출
@app.put("/qnas/{qna_id}", response_model=schemas.QnaResponse)
def update_qna(qna_id: int, updates: schemas.QnaUpdate, db: Session = Depends(get_db)):
    qna_record = crud.get_qna_by_id(db=db, qna_id=qna_id)
    
    if not qna_record:
        raise HTTPException(status_code=404, detail="QnA not found")
    
    try:
        # 질문 업데이트 및 RunPod 재호출
        qna_record.question = updates.question
        answer = send_to_runpod(updates.question)  # 새로운 질문으로 RunPod 호출
        qna_record.answer = answer
        
        db.commit()
        db.refresh(qna_record)
        return qna_record
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Delete (DELETE) - QnA 삭제
@app.delete("/qnas/{qna_id}")
def delete_qnas(qna_id: int, db: Session = Depends(get_db)):
    success = crud.delete_qna(db=db, qna_id=qna_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="QnA not found")
    
    return {"message": "QnA deleted successfully"}

# -------------------
# csv 파일 CRUD
# -------------------
# Create (POST) - CSV 생성
@app.post("/csvs/", response_model=schemas.CsvResponse)
def create_csv(csv: schemas.CsvCreate, db: Session = Depends(get_db)):
    return crud.create_csv(db=db, csv=csv)

# Read (GET all) - 모든 CSV 조회
@app.get("/csvs/", response_model=list[schemas.CsvResponse])
def read_csvs(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_csvs(db=db, skip=skip, limit=limit)

# Read (GET by file ID) - 특정 CSV 조회
@app.get("/csvs/{file_id}", response_model=schemas.CsvResponse)
def read_csv(file_id: int, db: Session = Depends(get_db)):
    csv_record = crud.get_csv_by_file_id(db=db, file_id=file_id)
    
    if not csv_record:
        raise HTTPException(status_code=404, detail="CSV not found")
    
    return csv_record

# Update (PUT) - CSV 정보 수정
@app.put("/csvs/{file_id}", response_model=schemas.CsvResponse)
def update_csv(file_id: int, updates: schemas.CsvUpdate, db: Session = Depends(get_db)):
    updated_csv = crud.update_csv(db=db, file_id=file_id, updates=updates)
    
    if not updated_csv:
        raise HTTPException(status_code=404, detail="CSV not found")
    
    return updated_csv

# Delete (DELETE) - CSV 삭제
@app.delete("/csvs/{file_id}")
def delete_csv(file_id: int, db: Session = Depends(get_db)):
    success = crud.delete_csv(db=db, file_id=file_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="CSV not found")
    
    return {"message": "CSV deleted successfully"}

# -------------------
# pdf 파일 CRUD
# -------------------
# Create (POST) - PDF 생성
@app.post("/pdfs/", response_model=schemas.PdfResponse)
def create_pdf(pdf: schemas.PdfCreate, db: Session = Depends(get_db)):
    return crud.create_pdf(db=db, pdf=pdf)

# Read (GET all) - 모든 PDF 조회
@app.get("/pdfs/", response_model=list[schemas.PdfResponse])
def read_pdfs(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    return crud.get_pdfs(db=db, skip=skip, limit=limit)

# Read (GET by file ID) - 특정 PDF 조회
@app.get("/pdfs/{file_id}", response_model=schemas.PdfResponse)
def read_pdf(file_id: int, db: Session = Depends(get_db)):
    pdf_record = crud.get_pdf_by_file_id(db=db, file_id=file_id)
    
    if not pdf_record:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return pdf_record

# Update (PUT) - PDF 정보 수정
@app.put("/pdfs/{file_id}", response_model=schemas.PdfResponse)
def update_pdf(file_id: int, updates: schemas.PdfUpdate, db: Session = Depends(get_db)):
    updated_pdf = crud.update_pdf(db=db, file_id=file_id, updates=updates)
    
    if not updated_pdf:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return updated_pdf

# Delete (DELETE) - PDF 삭제
@app.delete("/pdfs/{file_id}")
def delete_pdf(file_id: int, db: Session = Depends(get_db)):
    success = crud.delete_pdf(db=db, file_id=file_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    return {"message": "PDF deleted successfully"}

# -------------------
# 파이썬 서버 실행
# -------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", reload=True)