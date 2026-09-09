from db import charge_rent_period


def rent(total_days=10, period_days=7):
    return {
        "id": 1, "client_id": 42, "client_name": "Test",
        "amount": 100, "due_amount": 100 if period_days == 7 else 43,
        "return_date": "01.01.2026", "current_week": 1,
        "total_weeks": 2, "paid_days": 0, "period_days": period_days,
        "total_days": total_days, "status": "active",
    }


def test_partial_last_period_uses_ceil(db_conn):
    db_conn.execute("INSERT INTO clients (tg_id, balance) VALUES (42, 200)")
    r = rent(total_days=10)
    cursor = db_conn.cursor()
    assert charge_rent_period(cursor, r, "auto")["new_paid_days"] == 7
    db_conn.commit()
    r.update({"paid_days": 7, "period_days": 3, "due_amount": 43,
              "return_date": "08.01.2026"})
    result = charge_rent_period(cursor, r, "auto")
    assert result["charged"] == 43
    assert result["completed"] is True


def test_completed_contract_and_idempotency(db_conn):
    db_conn.execute("INSERT INTO clients (tg_id, balance) VALUES (42, 500)")
    cursor = db_conn.cursor()
    r = rent(total_days=7)
    result = charge_rent_period(cursor, r, "auto")
    assert result["completed"] is True
    db_conn.commit()
    assert charge_rent_period(cursor, r, "auto") is None
    assert charge_rent_period(cursor, r, "manual") is None
    assert cursor.execute("SELECT balance FROM clients WHERE tg_id=42").fetchone()[0] == 400
