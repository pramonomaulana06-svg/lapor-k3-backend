from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class Tanggapan(BaseModel):
    pesan: str
    waktu: datetime = Field(default_factory=datetime.utcnow)

class ReportCreate(BaseModel):
    jenis: str
    kategori: str
    judul: str
    detail: str
    nama: Optional[str] = None
    kontak: Optional[str] = None
    anonim: bool = False

class ReportDB(ReportCreate):
    kode: str
    lampiran: List[str] = []
    status: str = "menunggu"
    tanggapan: List[Tanggapan] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    selesai_at: Optional[datetime] = None