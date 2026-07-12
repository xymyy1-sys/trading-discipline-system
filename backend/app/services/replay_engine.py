import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.trading import ActionRecommendation, ExpectationSnapshot, IntradayEvidenceEvent, PositionStateHistory, VolumePriceSnapshot
from app.schemas.trading import ReplayCheckpoint, ReplayFrame, ReplayReportOut


def _items(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except Exception:
        return []


class ReplayEngine:
    def __init__(self, db: Session):
        self.db = db

    def replay(self, code: str, trade_date: str) -> ReplayReportOut:
        aliases = [code, code.lstrip("0")]
        frames: list[ReplayFrame] = []
        name = ""
        for row in self.db.query(ExpectationSnapshot).filter(ExpectationSnapshot.code.in_(aliases), ExpectationSnapshot.trade_date == trade_date).all():
            name = name or row.name
            frames.append(ReplayFrame(timestamp=row.created_at, frame_type="expectation", state=row.expectation_result, action=row.suggestion, evidence=_items(row.evidence_json)))
        for row in self.db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code.in_(aliases), VolumePriceSnapshot.trade_date == trade_date).all():
            name = name or row.name
            frames.append(ReplayFrame(timestamp=row.captured_at, frame_type="volume_price", state=row.pattern, price=row.price, vwap=row.vwap, data_quality=row.data_quality, evidence=_items(row.evidence_json)))
        for row in self.db.query(IntradayEvidenceEvent).filter(IntradayEvidenceEvent.target_code.in_(aliases), IntradayEvidenceEvent.trade_date == trade_date).all():
            name = name or row.target_name
            frames.append(ReplayFrame(timestamp=row.captured_at, frame_type="event", state=row.event_type, evidence=_items(row.evidence_json)))
        for row in self.db.query(PositionStateHistory).filter(PositionStateHistory.code.in_(aliases), PositionStateHistory.trade_date == trade_date).all():
            name = name or row.name
            frames.append(ReplayFrame(timestamp=row.captured_at, frame_type="state_transition", state=row.new_state, action=row.reason, evidence=_items(row.evidence_json)))
        for row in self.db.query(ActionRecommendation).filter(ActionRecommendation.code.in_(aliases), ActionRecommendation.trade_date == trade_date).all():
            name = name or row.name
            frames.append(ReplayFrame(timestamp=row.created_at, frame_type="recommendation", state=row.state, action=row.action, evidence=_items(row.evidence_json)))
        frames.sort(key=lambda item: item.timestamp)
        checkpoints: list[ReplayCheckpoint] = []
        if code in {"600584", "60584"}:
            expected = [("09:30", "YELLOW"), ("09:45", "VWAP"), ("10:05", "ORANGE"), ("10:20", "REDUCE")]
            for expected_time, signal in expected:
                matched = next((frame for frame in frames if signal.lower() in f"{frame.state} {frame.action} {' '.join(frame.evidence)}".lower()), None)
                checkpoints.append(ReplayCheckpoint(expected_time=expected_time, expected_signal=signal, matched=matched is not None, matched_time=matched.timestamp if matched else None))
        summary = [f"回放帧 {len(frames)} 条", f"事件 {sum(frame.frame_type == 'event' for frame in frames)} 条", f"建议 {sum(frame.frame_type == 'recommendation' for frame in frames)} 条"]
        complete = bool(frames) and (not checkpoints or all(item.matched for item in checkpoints))
        return ReplayReportOut(code=code, name=name, trade_date=trade_date, generated_at=datetime.now(), complete=complete, frames=frames, checkpoints=checkpoints, summary=summary)
