from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import Optional, List
import os, secrets, io, csv, shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv
import auth, models

load_dotenv()
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Lapor K3 API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AsyncIOMotorClient(os.getenv("MONGO_URL", "mongodb://localhost:27017"))
db = client.lapor_k3
reports_collection = db.reports

ADMIN_PASSWORD_HASH = auth.get_password_hash(os.getenv("ADMIN_PASSWORD", "admin123"))

def verify_admin(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    if not auth.decode_token(token):
        raise HTTPException(status_code=401, detail="Token invalid/expired")
    return True

# --- PUBLIC API ---

@app.post("/api/reports")
async def create_report(
    jenis: str = Form(...), kategori: str = Form(...), judul: str = Form(...),
    detail: str = Form(...), nama: Optional[str] = Form(None), kontak: Optional[str] = Form(None),
    anonim: bool = Form(False), file: Optional[UploadFile] = File(None)
):
    kode = "K3-" + secrets.token_hex(4).upper()
    lampiran_url = None
    
    if file:
        if file.size > 10 * 1024 * 1024:
            raise HTTPException(400, "File maksimal 10MB")
        os.makedirs("uploads", exist_ok=True)
        file_path = f"uploads/{kode}_{file.filename}"
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        lampiran_url = f"/uploads/{kode}_{file.filename}"

    doc = models.ReportDB(
        kode=kode, jenis=jenis, kategori=kategori, judul=judul, detail=detail,
        nama=None if anonim else nama, kontak=kontak, anonim=anonim,
        lampiran=[lampiran_url] if lampiran_url else []
    )
    
    await reports_collection.insert_one(doc.model_dump())
    return {"kode": kode}

@app.get("/api/reports/track/{kode}")
@limiter.limit("10/minute")
async def track_report(request: Request, kode: str):
    report = await reports_collection.find_one({"kode": kode})
    if not report:
        raise HTTPException(404, "Kode tidak ditemukan")
    return {
        "kode": report["kode"], "jenis": report["jenis"], "kategori": report["kategori"],
        "judul": report["judul"], "status": report["status"], "created_at": report["created_at"],
        "tanggapan": report["tanggapan"]
    }

@app.get("/api/stats")
async def get_stats():
    total = await reports_collection.count_documents({})
    menunggu = await reports_collection.count_documents({"status": "menunggu"})
    ditangani = await reports_collection.count_documents({"status": "ditangani"})
    selesai = await reports_collection.count_documents({"status": "selesai"})
    persen_selesai = round((selesai / total * 100)) if total > 0 else 0
    
    # Data statis dummy untuk 6 bulan (bisa dikembangkan dengan agregasi mongoDB)
    bulan_ini = datetime.utcnow()
    data_bulanan = []
    for i in range(6):
        data_bulanan.append({
            "bulan": (bulan_ini - timedelta(days=i*30)).strftime("%b"),
            "masuk": 0, "selesai": 0
        })
        
    return {
        "total": total, "menunggu": menunggu, "ditangani": ditangani, "selesai": selesai,
        "persen_selesai": persen_selesai, "grafik_6_bulan": data_bulanan[::-1]
    }

# --- ADMIN API ---

@app.post("/api/admin/login")
@limiter.limit("5/10minutes")
async def admin_login(request: Request):
    data = await request.json()
    if not auth.verify_password(data.get("password"), ADMIN_PASSWORD_HASH):
        raise HTTPException(401, "Password salah. Silakan coba lagi.")
    return {"access_token": auth.create_access_token({"sub": "admin"}), "token_type": "bearer"}

@app.get("/api/admin/reports")
async def admin_get_reports(authorization: str = Header(None)):
    verify_admin(authorization)
    cursor = reports_collection.find({}).sort("created_at", -1)
    reports = await cursor.to_list(length=1000)
    for r in reports: r["_id"] = str(r["_id"])
    return reports

@app.patch("/api/admin/reports/{id}/status")
async def update_status(id: str, request: Request, authorization: str = Header(None)):
    verify_admin(authorization)
    from bson import ObjectId
    data = await request.json()
    update_data = {"status": data.get("status"), "updated_at": datetime.utcnow()}
    if data.get("status") == "selesai": update_data["selesai_at"] = datetime.utcnow()
    await reports_collection.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    return {"message": "Status diperbarui"}

@app.post("/api/admin/reports/{id}/tanggapan")
async def add_tanggapan(id: str, request: Request, authorization: str = Header(None)):
    verify_admin(authorization)
    from bson import ObjectId
    data = await request.json()
    tanggapan = models.Tanggapan(pesan=data.get("pesan")).model_dump()
    await reports_collection.update_one({"_id": ObjectId(id)}, {"$push": {"tanggapan": tanggapan}})
    return {"message": "Tanggapan ditambahkan"}

@app.get("/api/admin/export")
async def export_csv(authorization: str = Header(None)):
    verify_admin(authorization)
    reports = await reports_collection.find({}).to_list(length=5000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Kode", "Tanggal", "Jenis", "Kategori", "Judul", "Status", "Nama"])
    for r in reports:
        writer.writerow([r["kode"], r["created_at"].strftime("%Y-%m-%d"), r["jenis"], r["kategori"], r["judul"], r["status"], "Anonim" if r["anonim"] else r["nama"]])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=laporan_k3.csv"})