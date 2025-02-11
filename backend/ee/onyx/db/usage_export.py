import uuid
from collections.abc import Generator
from datetime import datetime
from typing import IO
from typing import Optional

from fastapi_users_db_sqlalchemy import UUID_ID
from sqlalchemy.orm import Session

from ee.onyx.db.query_history import fetch_chat_sessions_eagerly_by_time
from ee.onyx.server.reporting.usage_export_models import ChatMessageSkeleton
from ee.onyx.server.reporting.usage_export_models import FlowType
from ee.onyx.server.reporting.usage_export_models import UsageReportMetadata
from onyx.configs.constants import MessageType
from onyx.db.models import UsageReport
from onyx.file_store.file_store import get_default_file_store


# Gets skeletons of all message
def get_empty_chat_messages_entries__paginated(
    db_session: Session,
    period: tuple[datetime, datetime],
    limit: int | None = 500,
    initial_time: datetime | None = None,
) -> tuple[Optional[datetime], list[ChatMessageSkeleton]]:
    chat_sessions = fetch_chat_sessions_eagerly_by_time(
        start=period[0],
        end=period[1],
        db_session=db_session,
        limit=limit,
        initial_time=initial_time,
    )

    message_skeletons: list[ChatMessageSkeleton] = []
    for chat_session in chat_sessions:
        flow_type = FlowType.SLACK if chat_session.onyxbot_flow else FlowType.CHAT

        for message in chat_session.messages:
            # Only count user messages
            if message.message_type != MessageType.USER:
                continue

            message_skeletons.append(
                ChatMessageSkeleton(
                    message_id=message.id,
                    chat_session_id=chat_session.id,
                    user_id=str(chat_session.user_id) if chat_session.user_id else None,
                    flow_type=flow_type,
                    time_sent=message.time_sent,
                )
            )
    if len(chat_sessions) == 0:
        return None, []

    return chat_sessions[0].time_created, message_skeletons


def get_all_empty_chat_message_entries(
    db_session: Session,
    period: tuple[datetime, datetime],
) -> Generator[list[ChatMessageSkeleton], None, None]:
    initial_time: Optional[datetime] = period[0]
    ind = 0
    while True:
        ind += 1

        time_created, message_skeletons = get_empty_chat_messages_entries__paginated(
            db_session,
            period,
            initial_time=initial_time,
        )

        if not message_skeletons:
            return

        yield message_skeletons

        # Update initial_time for the next iteration
        initial_time = time_created


def get_all_usage_reports(db_session: Session) -> list[UsageReportMetadata]:
    return [
        UsageReportMetadata(
            report_name=r.report_name,
            requestor=str(r.requestor_user_id) if r.requestor_user_id else None,
            time_created=r.time_created,
            period_from=r.period_from,
            period_to=r.period_to,
        )
        for r in db_session.query(UsageReport).all()
    ]


def get_usage_report_data(
    db_session: Session,
    report_name: str,
) -> IO:
    file_store = get_default_file_store(db_session)
    # usage report may be very large, so don't load it all into memory
    return file_store.read_file(file_name=report_name, mode="b", use_tempfile=True)


def write_usage_report(
    db_session: Session,
    report_name: str,
    user_id: uuid.UUID | UUID_ID | None,
    period: tuple[datetime, datetime] | None,
) -> UsageReport:
    new_report = UsageReport(
        report_name=report_name,
        requestor_user_id=user_id,
        period_from=period[0] if period else None,
        period_to=period[1] if period else None,
    )
    db_session.add(new_report)
    db_session.commit()
    return new_report
