from datetime import datetime, timezone
import requests
from typing import Any, List, Optional

from danswer.configs.app_configs import INDEX_BATCH_SIZE
from danswer.configs.constants import DocumentSource
from danswer.connectors.interfaces import GenerateDocumentsOutput, LoadConnector, PollConnector, SecondsSinceUnixEpoch
from danswer.connectors.models import BasicExpertInfo, Document, Section, ConnectorMissingCredentialError

# Use appropriate base URL and API base URL for Paperless
PAPERLESS_BASE_URL = "http://host.docker.internal:8000"
PAPERLESS_API_TOKEN = "e34006ee506c19615f79d0472104019ac3f61968"

class PaperlessConnector(LoadConnector, PollConnector):
    def __init__(self, api_token: Optional[str] = None) -> None:
        self.api_token = api_token
        self.base_url = PAPERLESS_BASE_URL

    def load_credentials(self, credentials: dict[str, Any]) -> Optional[dict[str, Any]]:
        self.api_token = credentials.get("paperless_api_token")
        self.base_url = credentials.get("paperless_base_url")
        return None

    def _make_request(self, endpoint: str, params: Optional[dict] = None) -> Any:
        if not self.api_token:
            raise ConnectorMissingCredentialError("Missing API token for Paperless")

        headers = {"Authorization": f"Token {self.api_token}"}
        # 确保所有请求都显式要求JSON格式的响应
        if params is None:
            params = {}
        params['format'] = 'json'  # 添加format参数

        #构建完整的请求URL
        url = f"{self.base_url}/api/{endpoint}/"
        response = requests.get(url, headers=headers, params=params, verify=False)  # 注意verify参数根据您的需要决定是否使用
        # 打印响应状态码和响应内容，帮助调试
        print("Headers Sent:", headers)
        print("URL Requested:", url)
        print(f"Response Status Code: {response.status_code}")

        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.text}")
        try:
            return response.json()
        except ValueError as e:
            raise ValueError(f"Error parsing JSON from response: {e}")


    def _fetch_documents(self, document_type_id: Optional[int] = None, start: datetime | None = None, end: datetime | None = None) -> List[Document]:
        params = {}
        if document_type_id is not None:
            params['document_type__id'] = document_type_id  # Add the document type ID to the parameters
        if start:
            params['added__gte'] = start.strftime('%Y-%m-%dT%H:%M:%SZ')
        if end:
            params['added__lte'] = end.strftime('%Y-%m-%dT%H:%M:%SZ')

        documents_data = self._make_request("documents", params)
        documents: list[Document] = []
        for doc in documents_data['results']:
            documents.append(self._process_document(doc))
            yield documents
            
       

    def _process_document(self, doc: dict) -> Document:
        doc_id = str(doc['id']) if 'id' in doc else doc['title']
        doc_link = f"{self.base_url}/api/documents/{doc_id}/download/"

        # We could potentially extract and use tags, notes, or custom fields if they are relevant.
        tags = [f"Tag ID: {tag}" for tag in doc.get('tags', [])]
        custom_fields = doc.get('custom_fields', [])

        # Construct the document text or content from multiple fields if needed.
        # Here, we combine title and original file name for the document text.
        #doc_text = f"Title: {doc['title']}\nOriginal File: {doc.get('original_file_name', 'No file name')}"
        # Construct the document text with title, and content
        doc_text = f"Title: {doc['title']}\nContent: {doc.get('content', 'No content available')}"


        return Document(
            id = doc_id,
            sections=[Section(link=doc_link, text=doc_text)],
            source=DocumentSource.PAPERLESS,
            semantic_identifier=doc['title'],
            doc_updated_at=datetime.fromisoformat(doc['modified']),
            primary_owners=[BasicExpertInfo(display_name='Unknown')],  # Example placeholder, modify as needed
            metadata={
                "original_file_name": doc.get('original_file_name'),
                "archived_file_name": doc.get('archived_file_name'),
                "tags": tags,
                "custom_fields": custom_fields
            }
    )


    def load_from_state(self) -> GenerateDocumentsOutput:
        yield from self._fetch_documents(document_type_id=1)

    def poll_source(self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch) -> GenerateDocumentsOutput:
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc)
        end_datetime = datetime.fromtimestamp(end, tz=timezone.utc)
        yield from self._fetch_documents(1, start_datetime, end_datetime)

# Main execution example
if __name__ == "__main__":
    paperless_connector = PaperlessConnector(api_token=PAPERLESS_API_TOKEN)
    latest_docs = paperless_connector.load_from_state()
    for doc in latest_docs:
        print(doc)
    # headers = {"Authorization": f"Token {PAPERLESS_API_TOKEN}"}
    # url = "http://localhost:8000/api/documents/?format=json"

    # response = requests.get(url, headers=headers)
    # print("Status Code:", response.status_code)
    # print("Headers Sent:", headers)
    # print("URL Requested:", url)
