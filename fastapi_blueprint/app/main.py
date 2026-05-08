from fastapi import FastAPI

app = FastAPI(title="Metrology Data Collection Platform")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def root():
    return {
        "name": "Metrology Data Collection Platform",
        "status": "blueprint",
        "next": "Move V1.7 routes and collector logic into API/services modules.",
    }
