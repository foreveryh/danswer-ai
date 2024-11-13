from collections import defaultdict

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy.orm import Session

from danswer.auth.users import current_admin_user
from danswer.auth.users import current_curator_or_admin_user
from danswer.db.engine import get_session
from danswer.db.models import User
from danswer.db.token_limit import fetch_all_user_token_rate_limits
from danswer.db.token_limit import insert_user_token_rate_limit
from danswer.server.query_and_chat.token_limit import any_rate_limit_exists
from danswer.server.token_rate_limits.models import TokenRateLimitArgs
from danswer.server.token_rate_limits.models import TokenRateLimitDisplay
from ee.danswer.db.token_limit import fetch_all_user_group_token_rate_limits_by_group
from ee.danswer.db.token_limit import fetch_user_group_token_rate_limits
from ee.danswer.db.token_limit import insert_user_group_token_rate_limit

router = APIRouter(prefix="/admin/token-rate-limits")


"""
Group Token Limit Settings
"""


@router.get("/user-groups")
def get_all_group_token_limit_settings(
    _: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> dict[str, list[TokenRateLimitDisplay]]:
    user_groups_to_token_rate_limits = fetch_all_user_group_token_rate_limits_by_group(
        db_session
    )

    token_rate_limits_by_group = defaultdict(list)
    for token_rate_limit, group_name in user_groups_to_token_rate_limits:
        token_rate_limits_by_group[group_name].append(
            TokenRateLimitDisplay.from_db(token_rate_limit)
        )

    return dict(token_rate_limits_by_group)


@router.get("/user-group/{group_id}")
def get_group_token_limit_settings(
    group_id: int,
    user: User | None = Depends(current_curator_or_admin_user),
    db_session: Session = Depends(get_session),
) -> list[TokenRateLimitDisplay]:
    return [
        TokenRateLimitDisplay.from_db(token_rate_limit)
        for token_rate_limit in fetch_user_group_token_rate_limits(
            db_session, group_id, user
        )
    ]


@router.post("/user-group/{group_id}")
def create_group_token_limit_settings(
    group_id: int,
    token_limit_settings: TokenRateLimitArgs,
    _: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> TokenRateLimitDisplay:
    rate_limit_display = TokenRateLimitDisplay.from_db(
        insert_user_group_token_rate_limit(
            db_session=db_session,
            token_rate_limit_settings=token_limit_settings,
            group_id=group_id,
        )
    )
    # clear cache in case this was the first rate limit created
    any_rate_limit_exists.cache_clear()
    return rate_limit_display


"""
User Token Limit Settings
"""


@router.get("/users")
def get_user_token_limit_settings(
    _: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> list[TokenRateLimitDisplay]:
    return [
        TokenRateLimitDisplay.from_db(token_rate_limit)
        for token_rate_limit in fetch_all_user_token_rate_limits(db_session)
    ]


@router.post("/users")
def create_user_token_limit_settings(
    token_limit_settings: TokenRateLimitArgs,
    _: User | None = Depends(current_admin_user),
    db_session: Session = Depends(get_session),
) -> TokenRateLimitDisplay:
    rate_limit_display = TokenRateLimitDisplay.from_db(
        insert_user_token_rate_limit(db_session, token_limit_settings)
    )
    # clear cache in case this was the first rate limit created
    any_rate_limit_exists.cache_clear()
    return rate_limit_display
