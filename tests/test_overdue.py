import datetime
from db import should_send_overdue_reminder


TODAY = datetime.date(2026, 1, 15)


def notified():
    return "14.01.2026"


def test_first_notification_is_immediate():
    assert should_send_overdue_reminder(TODAY, None, TODAY)


def test_overdue_cadence_boundaries():
    for days in (1, 3, 7, 14):
        due = TODAY - datetime.timedelta(days=days)
        assert should_send_overdue_reminder(due, notified(), TODAY)
    for days in (0, 2, 8):
        due = TODAY - datetime.timedelta(days=days)
        assert not should_send_overdue_reminder(due, notified(), TODAY)


def test_same_or_future_notification_date_is_suppressed():
    due = TODAY - datetime.timedelta(days=7)
    assert not should_send_overdue_reminder(due, "15.01.2026", TODAY)
    assert not should_send_overdue_reminder(due, "16.01.2026", TODAY)
