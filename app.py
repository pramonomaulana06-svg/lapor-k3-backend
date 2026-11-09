from fastapi import FastAPI, Depends, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import Optional, List
from pydantic import BaseModel
import os, secrets, io, csv
from datetime import datetime, timedelta
from dotenv import load_dotenv
import auth, models

load_dotenv()

app = FastAPI(title="Lapor K3 API")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    mongo_url = os.getenv("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    return client.lapor_k3.reports

# FUNGSI INI SUDAH DIPERBAIKI
def get_admin_hash():
    return auth.get_password_hash("admin123")

def verify_admin(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ")[1]
    if not auth.decode_token(token):
        raise HTTPException(status_code=401, detail="Token invalid/expired")
    return True

class ReportCreateJSON(BaseModel):
    jenis: str
    kategori: str
    judul: str
    detail: str
    nama: Optional[str] = None
    kontak: Optional[str] = None
    anonim: bool = False
    lampiran_base64: Optional[str] = None

# --- PUBLIC API ---

@app.post("/api/reports")
async def create_report(data: ReportCreateJSON):
    kode = "K3-" + secrets.token_hex(4).upper()
    
    doc = models.ReportDB(
        kode=kode, jenis=data.jenis, kategori=data.kategori, judul=data.judul, 
        detail=data.detail, nama=None if data.anonim else data.nama, 
        kontak=data.kontak, anonim=data.anonim,
        lampiran=[data.lampiran_base64] if data.lampiran_base64 else []
    )
    
    db = get_db()
    await db.insert_one(doc.model_dump())
    return {"kode": kode}

@app.get("/api/reports/track/{kode}")
@limiter.limit("10/minute")
async def track_report(request: Request, kode: str):
    db = get_db()
    report = await db.find_one({"kode": kode})
    if not report:
        raise HTTPException(404, "Kode tidak ditemukan")
    
    created_at = report.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    return {
        "kode": report["kode"], "jenis": report["jenis"], "kategori": report["kategori"],
        "judul": report["judul"], "status": report["status"], "created_at": created_at,
        "tanggapan": report.get("tanggapan", [])
    }

@app.get("/api/stats")
async def get_stats():
    db = get_db()
    total = await db.count_documents({})
    menunggu = await db.count_documents({"status": "menunggu"})
    ditangani = await db.count_documents({"status": "ditangani"})
    selesai = await db.count_documents({"status": "selesai"})
    persen_selesai = round((selesai / total * 100)) if total > 0 else 0
    
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
    admin_hash = get_admin_hash() 
    if not auth.verify_password(data.get("password"), admin_hash):
        raise HTTPException(401, "Password salah. Silakan coba lagi.")
    return {"access_token": auth.create_access_token({"sub": "admin"}), "token_type": "bearer"}

@app.get("/api/admin/reports")
async def admin_get_reports(authorization: str = Header(None)):
    verify_admin(authorization)
    db = get_db()
    cursor = db.find({}).sort("created_at", -1)
    reports = await cursor.to_list(length=1000)
    
    for r in reports: 
        r["_id"] = str(r["_id"])
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
            
    return reports

@app.patch("/api/admin/reports/{id}/status")
async def update_status(id: str, request: Request, authorization: str = Header(None)):
    verify_admin(authorization)
    from bson import ObjectId
    db = get_db()
    data = await request.json()
    update_data = {"status": data.get("status"), "updated_at": datetime.utcnow()}
    if data.get("status") == "selesai": update_data["selesai_at"] = datetime.utcnow()
    await db.update_one({"_id": ObjectId(id)}, {"$set": update_data})
    return {"message": "Status diperbarui"}

@app.post("/api/admin/reports/{id}/tanggapan")
async def add_tanggapan(id: str, request: Request, authorization: str = Header(None)):
    verify_admin(authorization)
    from bson import ObjectId
    db = get_db()
    data = await request.json()
    tanggapan = models.Tanggapan(pesan=data.get("pesan")).model_dump()
    await db.update_one({"_id": ObjectId(id)}, {"$push": {"tanggapan": tanggapan}})
    return {"message": "Tanggapan ditambahkan"}

@app.get("/api/admin/export")
async def export_csv(authorization: str = Header(None)):
    verify_admin(authorization)
    db = get_db()
    reports = await db.find({}).to_list(length=5000)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Kode", "Tanggal", "Jenis", "Kategori", "Judul", "Status", "Nama"])
    for r in reports:
        tgl = r["created_at"].strftime("%Y-%m-%d") if isinstance(r.get("created_at"), datetime) else str(r.get("created_at"))
        writer.writerow([r["kode"], tgl, r["jenis"], r["kategori"], r["judul"], r["status"], "Anonim" if r["anonim"] else r["nama"]])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=laporan_k3.csv"})