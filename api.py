from fastapi import FastAPI
import sqlite3

app = FastAPI()

@app.get("/status")
def get_trading_status():
    conn = sqlite3.connect("trading_log.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM trades")
    rows = cursor.fetchall()
    conn.close()
    
    # Simple win rate calculation logic can be added here later
    return {"total_trades": len(rows), "trades": rows}