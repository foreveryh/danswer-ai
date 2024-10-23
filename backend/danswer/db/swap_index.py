from sqlalchemy.orm import Session

from danswer.configs.app_configs import MULTI_TENANT
from danswer.configs.constants import KV_REINDEX_KEY
from danswer.db.connector_credential_pair import get_connector_credential_pairs
from danswer.db.connector_credential_pair import resync_cc_pair
from danswer.db.enums import IndexModelStatus
from danswer.db.index_attempt import cancel_indexing_attempts_past_model
from danswer.db.index_attempt import (
    count_unique_cc_pairs_with_successful_index_attempts,
)
from danswer.db.models import SearchSettings
from danswer.db.search_settings import get_current_search_settings
from danswer.db.search_settings import get_secondary_search_settings
from danswer.db.search_settings import update_search_settings_status
from danswer.key_value_store.factory import get_kv_store
from danswer.utils.logger import setup_logger


logger = setup_logger()


def check_index_swap(db_session: Session) -> SearchSettings | None:
    """Get count of cc-pairs and count of successful index_attempts for the
    new model grouped by connector + credential, if it's the same, then assume
    new index is done building. If so, swap the indices and expire the old one."""
    # Default CC-pair created for Ingestion API unused here
    all_cc_pairs = get_connector_credential_pairs(db_session)
    cc_pair_count = max(len(all_cc_pairs) - 1, 0)
    search_settings = get_secondary_search_settings(db_session)

    if not search_settings:
        return None

    unique_cc_indexings = count_unique_cc_pairs_with_successful_index_attempts(
        search_settings_id=search_settings.id, db_session=db_session
    )

    # Index Attempts are cleaned up as well when the cc-pair is deleted so the logic in this
    # function is correct. The unique_cc_indexings are specifically for the existing cc-pairs
    if unique_cc_indexings > cc_pair_count:
        logger.error("More unique indexings than cc pairs, should not occur")

    if cc_pair_count == 0 or cc_pair_count == unique_cc_indexings:
        # Swap indices
        now_old_search_settings = get_current_search_settings(db_session)
        update_search_settings_status(
            search_settings=now_old_search_settings,
            new_status=IndexModelStatus.PAST,
            db_session=db_session,
        )

        update_search_settings_status(
            search_settings=search_settings,
            new_status=IndexModelStatus.PRESENT,
            db_session=db_session,
        )

        if cc_pair_count > 0:
            kv_store = get_kv_store()
            kv_store.store(KV_REINDEX_KEY, False)

            # Expire jobs for the now past index/embedding model
            cancel_indexing_attempts_past_model(db_session)

            # Recount aggregates
            for cc_pair in all_cc_pairs:
                resync_cc_pair(cc_pair, db_session=db_session)

            if MULTI_TENANT:
                return now_old_search_settings
    return None
