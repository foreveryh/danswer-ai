from uuid import uuid4

import requests

from onyx.configs.constants import DocumentSource
from onyx.db.enums import AccessType
from tests.integration.common_utils.constants import API_SERVER_URL
from tests.integration.common_utils.constants import GENERAL_HEADERS
from tests.integration.common_utils.constants import NUM_DOCS
from tests.integration.common_utils.managers.api_key import DATestAPIKey
from tests.integration.common_utils.managers.cc_pair import DATestCCPair
from tests.integration.common_utils.test_models import DATestUser
from tests.integration.common_utils.test_models import SimpleTestDocument
from tests.integration.common_utils.vespa import vespa_fixture


def _verify_document_permissions(
    retrieved_doc: dict,
    cc_pair: DATestCCPair,
    doc_set_names: list[str] | None = None,
    group_names: list[str] | None = None,
    doc_creating_user: DATestUser | None = None,
) -> None:
    acl_keys = set(retrieved_doc.get("access_control_list", {}).keys())
    print(f"ACL keys: {acl_keys}")

    if cc_pair.access_type == AccessType.PUBLIC:
        if "PUBLIC" not in acl_keys:
            raise ValueError(
                f"Document {retrieved_doc['document_id']} is public but"
                " does not have the PUBLIC ACL key"
            )

    if doc_creating_user is not None:
        if f"user_email:{doc_creating_user.email}" not in acl_keys:
            raise ValueError(
                f"Document {retrieved_doc['document_id']} was created by user"
                f" {doc_creating_user.email} but does not have the user_email:{doc_creating_user.email} ACL key"
            )

    if group_names is not None:
        expected_group_keys = {f"group:{group_name}" for group_name in group_names}
        found_group_keys = {key for key in acl_keys if key.startswith("group:")}
        if found_group_keys != expected_group_keys:
            raise ValueError(
                f"Document {retrieved_doc['document_id']} has incorrect group ACL keys. "
                f"Expected: {expected_group_keys}  Found: {found_group_keys}\n"
                f"All ACL keys: {acl_keys}"
            )

    if doc_set_names is not None:
        found_doc_set_names = set(retrieved_doc.get("document_sets", {}).keys())
        if found_doc_set_names != set(doc_set_names):
            raise ValueError(
                f"Document set names mismatch. \nFound: {found_doc_set_names}, \n"
                f"Expected: {set(doc_set_names)}"
            )


def _generate_dummy_document(
    document_id: str,
    cc_pair_id: int,
    content: str | None = None,
) -> dict:
    text = content if content else f"This is test document {document_id}"
    return {
        "document": {
            "id": document_id,
            "sections": [
                {
                    "text": text,
                    "link": f"{document_id}",
                }
            ],
            "source": DocumentSource.NOT_APPLICABLE,
            # just for testing metadata
            "metadata": {"document_id": document_id},
            "semantic_identifier": f"Test Document {document_id}",
            "from_ingestion_api": True,
        },
        "cc_pair_id": cc_pair_id,
    }


class DocumentManager:
    @staticmethod
    def seed_dummy_docs(
        cc_pair: DATestCCPair,
        num_docs: int = NUM_DOCS,
        document_ids: list[str] | None = None,
        api_key: DATestAPIKey | None = None,
    ) -> list[SimpleTestDocument]:
        # Use provided document_ids if available, otherwise generate random UUIDs
        if document_ids is None:
            document_ids = [f"test-doc-{uuid4()}" for _ in range(num_docs)]
        else:
            num_docs = len(document_ids)
        # Create and ingest some documents
        documents: list[dict] = []
        for document_id in document_ids:
            document = _generate_dummy_document(document_id, cc_pair.id)
            documents.append(document)
            response = requests.post(
                f"{API_SERVER_URL}/onyx-api/ingestion",
                json=document,
                headers=api_key.headers if api_key else GENERAL_HEADERS,
            )
            response.raise_for_status()

        print("Seeding completed successfully.")
        return [
            SimpleTestDocument(
                id=document["document"]["id"],
                content=document["document"]["sections"][0]["text"],
            )
            for document in documents
        ]

    @staticmethod
    def seed_doc_with_content(
        cc_pair: DATestCCPair,
        content: str,
        document_id: str | None = None,
        api_key: DATestAPIKey | None = None,
    ) -> SimpleTestDocument:
        # Use provided document_ids if available, otherwise generate random UUIDs
        if document_id is None:
            document_id = f"test-doc-{uuid4()}"
        # Create and ingest some documents
        document: dict = _generate_dummy_document(document_id, cc_pair.id, content)
        response = requests.post(
            f"{API_SERVER_URL}/onyx-api/ingestion",
            json=document,
            headers=api_key.headers if api_key else GENERAL_HEADERS,
        )
        response.raise_for_status()

        print("Seeding completed successfully.")

        return SimpleTestDocument(
            id=document["document"]["id"],
            content=document["document"]["sections"][0]["text"],
        )

    @staticmethod
    def verify(
        vespa_client: vespa_fixture,
        cc_pair: DATestCCPair,
        # If None, will not check doc sets or groups
        # If empty list, will check for empty doc sets or groups
        doc_set_names: list[str] | None = None,
        group_names: list[str] | None = None,
        doc_creating_user: DATestUser | None = None,
        verify_deleted: bool = False,
    ) -> None:
        doc_ids = [document.id for document in cc_pair.documents]
        retrieved_docs_dict = vespa_client.get_documents_by_id(doc_ids)["documents"]

        retrieved_docs = {
            doc["fields"]["document_id"]: doc["fields"] for doc in retrieved_docs_dict
        }

        # Left this here for debugging purposes.
        # import json
        # for doc in retrieved_docs.values():
        #     printable_doc = doc.copy()
        #     print(printable_doc.keys())
        #     printable_doc.pop("embeddings")
        #     printable_doc.pop("title_embedding")
        #     print(json.dumps(printable_doc, indent=2))

        for document in cc_pair.documents:
            retrieved_doc = retrieved_docs.get(document.id)
            if not retrieved_doc:
                if not verify_deleted:
                    raise ValueError(f"Document not found: {document.id}")
                continue
            if verify_deleted:
                raise ValueError(
                    f"Document found when it should be deleted: {document.id}"
                )
            _verify_document_permissions(
                retrieved_doc,
                cc_pair,
                doc_set_names,
                group_names,
                doc_creating_user,
            )
