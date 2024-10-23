import { Connector } from "@/lib/connectors/connectors";
import { Credential } from "@/lib/connectors/credentials";
import {
  DeletionAttemptSnapshot,
  IndexAttemptSnapshot,
  ValidStatuses,
} from "@/lib/types";

export enum ConnectorCredentialPairStatus {
  ACTIVE = "ACTIVE",
  PAUSED = "PAUSED",
  DELETING = "DELETING",
}

export interface CCPairFullInfo {
  id: number;
  name: string;
  status: ConnectorCredentialPairStatus;
  num_docs_indexed: number;
  connector: Connector<any>;
  credential: Credential<any>;
  number_of_index_attempts: number;
  last_index_attempt_status: ValidStatuses | null;
  latest_deletion_attempt: DeletionAttemptSnapshot | null;
  is_public: boolean;
  is_editable_for_current_user: boolean;
  deletion_failure_message: string | null;
  indexing: boolean;
}

export interface PaginatedIndexAttempts {
  index_attempts: IndexAttemptSnapshot[];
  page: number;
  total_pages: number;
}
