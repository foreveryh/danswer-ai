import { Button } from "@/components/Button";
import { PopupSpec } from "@/components/admin/connectors/Popup";
import { useState } from "react";
import { useSWRConfig } from "swr";
import * as Yup from "yup";
import { useRouter } from "next/navigation";
import { adminDeleteCredential } from "@/lib/credential";
import { setupGmailOAuth } from "@/lib/gmail";
import { GMAIL_AUTH_IS_ADMIN_COOKIE_NAME } from "@/lib/constants";
import Cookies from "js-cookie";
import { TextFormField } from "@/components/admin/connectors/Field";
import { Form, Formik } from "formik";
import { User } from "@/lib/types";
import CardSection from "@/components/admin/CardSection";
import {
  Credential,
  GmailCredentialJson,
  GmailServiceAccountCredentialJson,
} from "@/lib/connectors/credentials";

type GmailCredentialJsonTypes = "authorized_user" | "service_account";

const DriveJsonUpload = ({
  setPopup,
}: {
  setPopup: (popupSpec: PopupSpec | null) => void;
}) => {
  const { mutate } = useSWRConfig();
  const [credentialJsonStr, setCredentialJsonStr] = useState<
    string | undefined
  >();

  return (
    <>
      <input
        className={
          "mr-3 text-sm text-gray-900 border border-gray-300 overflow-visible " +
          "cursor-pointer bg-background dark:text-gray-400 focus:outline-none " +
          "dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400"
        }
        type="file"
        accept=".json"
        onChange={(event) => {
          if (!event.target.files) {
            return;
          }
          const file = event.target.files[0];
          const reader = new FileReader();

          reader.onload = function (loadEvent) {
            if (!loadEvent?.target?.result) {
              return;
            }
            const fileContents = loadEvent.target.result;
            setCredentialJsonStr(fileContents as string);
          };

          reader.readAsText(file);
        }}
      />

      <Button
        disabled={!credentialJsonStr}
        onClick={async () => {
          // check if the JSON is a app credential or a service account credential
          let credentialFileType: GmailCredentialJsonTypes;
          try {
            const appCredentialJson = JSON.parse(credentialJsonStr!);
            if (appCredentialJson.web) {
              credentialFileType = "authorized_user";
            } else if (appCredentialJson.type === "service_account") {
              credentialFileType = "service_account";
            } else {
              throw new Error(
                "Unknown credential type, expected 'OAuth Web application'"
              );
            }
          } catch (e) {
            setPopup({
              message: `Invalid file provided - ${e}`,
              type: "error",
            });
            return;
          }

          if (credentialFileType === "authorized_user") {
            const response = await fetch(
              "/api/manage/admin/connector/gmail/app-credential",
              {
                method: "PUT",
                headers: {
                  "Content-Type": "application/json",
                },
                body: credentialJsonStr,
              }
            );
            if (response.ok) {
              setPopup({
                message: "Successfully uploaded app credentials",
                type: "success",
              });
            } else {
              const errorMsg = await response.text();
              setPopup({
                message: `Failed to upload app credentials - ${errorMsg}`,
                type: "error",
              });
            }
            mutate("/api/manage/admin/connector/gmail/app-credential");
          }

          if (credentialFileType === "service_account") {
            const response = await fetch(
              "/api/manage/admin/connector/gmail/service-account-key",
              {
                method: "PUT",
                headers: {
                  "Content-Type": "application/json",
                },
                body: credentialJsonStr,
              }
            );
            if (response.ok) {
              setPopup({
                message: "Successfully uploaded app credentials",
                type: "success",
              });
            } else {
              const errorMsg = await response.text();
              setPopup({
                message: `Failed to upload app credentials - ${errorMsg}`,
                type: "error",
              });
            }
            mutate("/api/manage/admin/connector/gmail/service-account-key");
          }
        }}
      >
        Upload
      </Button>
    </>
  );
};

interface DriveJsonUploadSectionProps {
  setPopup: (popupSpec: PopupSpec | null) => void;
  appCredentialData?: { client_id: string };
  serviceAccountCredentialData?: { service_account_email: string };
  isAdmin: boolean;
}

export const GmailJsonUploadSection = ({
  setPopup,
  appCredentialData,
  serviceAccountCredentialData,
  isAdmin,
}: DriveJsonUploadSectionProps) => {
  const { mutate } = useSWRConfig();

  if (serviceAccountCredentialData?.service_account_email) {
    return (
      <div className="mt-2 text-sm">
        <div>
          Found existing service account key with the following <b>Email:</b>
          <p className="italic mt-1">
            {serviceAccountCredentialData.service_account_email}
          </p>
        </div>
        {isAdmin ? (
          <>
            <div className="mt-4 mb-1">
              If you want to update these credentials, delete the existing
              credentials through the button below, and then upload a new
              credentials JSON.
            </div>
            <Button
              onClick={async () => {
                const response = await fetch(
                  "/api/manage/admin/connector/gmail/service-account-key",
                  {
                    method: "DELETE",
                  }
                );
                if (response.ok) {
                  mutate(
                    "/api/manage/admin/connector/gmail/service-account-key"
                  );
                  setPopup({
                    message: "Successfully deleted service account key",
                    type: "success",
                  });
                } else {
                  const errorMsg = await response.text();
                  setPopup({
                    message: `Failed to delete service account key - ${errorMsg}`,
                    type: "error",
                  });
                }
              }}
            >
              Delete
            </Button>
          </>
        ) : (
          <>
            <div className="mt-4 mb-1">
              To change these credentials, please contact an administrator.
            </div>
          </>
        )}
      </div>
    );
  }

  if (appCredentialData?.client_id) {
    return (
      <div className="mt-2 text-sm">
        <div>
          Found existing app credentials with the following <b>Client ID:</b>
          <p className="italic mt-1">{appCredentialData.client_id}</p>
        </div>
        <div className="mt-4 mb-1">
          If you want to update these credentials, delete the existing
          credentials through the button below, and then upload a new
          credentials JSON.
        </div>
        <Button
          onClick={async () => {
            const response = await fetch(
              "/api/manage/admin/connector/gmail/app-credential",
              {
                method: "DELETE",
              }
            );
            if (response.ok) {
              mutate("/api/manage/admin/connector/gmail/app-credential");
              setPopup({
                message: "Successfully deleted service account key",
                type: "success",
              });
            } else {
              const errorMsg = await response.text();
              setPopup({
                message: `Failed to delete app credential - ${errorMsg}`,
                type: "error",
              });
            }
          }}
        >
          Delete
        </Button>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="mt-2">
        <p className="text-sm mb-2">
          Curators are unable to set up the Gmail credentials. To add a Gmail
          connector, please contact an administrator.
        </p>
      </div>
    );
  }

  return (
    <div className="mt-2">
      <p className="text-sm mb-2">
        Follow the guide{" "}
        <a
          className="text-link"
          target="_blank"
          href="https://docs.danswer.dev/connectors/gmail#authorization"
          rel="noreferrer"
        >
          here
        </a>{" "}
        to either (1) setup a google OAuth App in your company workspace or (2)
        create a Service Account.
        <br />
        <br />
        Download the credentials JSON if choosing option (1) or the Service
        Account key JSON if chooosing option (2), and upload it here.
      </p>
      <DriveJsonUpload setPopup={setPopup} />
    </div>
  );
};

interface DriveCredentialSectionProps {
  gmailPublicCredential?: Credential<GmailCredentialJson>;
  gmailServiceAccountCredential?: Credential<GmailServiceAccountCredentialJson>;
  serviceAccountKeyData?: { service_account_email: string };
  appCredentialData?: { client_id: string };
  setPopup: (popupSpec: PopupSpec | null) => void;
  refreshCredentials: () => void;
  connectorExists: boolean;
  user: User | null;
}

export const GmailAuthSection = ({
  gmailPublicCredential,
  gmailServiceAccountCredential,
  serviceAccountKeyData,
  appCredentialData,
  setPopup,
  refreshCredentials,
  connectorExists,
  user,
}: DriveCredentialSectionProps) => {
  const router = useRouter();

  const existingCredential =
    gmailPublicCredential || gmailServiceAccountCredential;
  if (existingCredential) {
    return (
      <>
        <p className="mb-2 text-sm">
          <i>Existing credential already setup!</i>
        </p>
        <Button
          onClick={async () => {
            if (connectorExists) {
              setPopup({
                message:
                  "Cannot revoke access to Gmail while any connector is still setup. Please delete all connectors, then try again.",
                type: "error",
              });
              return;
            }
            await adminDeleteCredential(existingCredential.id);
            setPopup({
              message: "Successfully revoked access to Gmail!",
              type: "success",
            });
            refreshCredentials();
          }}
        >
          Revoke Access
        </Button>
      </>
    );
  }

  if (serviceAccountKeyData?.service_account_email) {
    return (
      <div>
        <CardSection>
          <Formik
            initialValues={{
              google_primary_admin: user?.email || "",
            }}
            validationSchema={Yup.object().shape({
              google_primary_admin: Yup.string().required(),
            })}
            onSubmit={async (values, formikHelpers) => {
              formikHelpers.setSubmitting(true);

              const response = await fetch(
                "/api/manage/admin/connector/gmail/service-account-credential",
                {
                  method: "PUT",
                  headers: {
                    "Content-Type": "application/json",
                  },
                  body: JSON.stringify({
                    google_primary_admin: values.google_primary_admin,
                  }),
                }
              );

              if (response.ok) {
                setPopup({
                  message: "Successfully created service account credential",
                  type: "success",
                });
              } else {
                const errorMsg = await response.text();
                setPopup({
                  message: `Failed to create service account credential - ${errorMsg}`,
                  type: "error",
                });
              }
              refreshCredentials();
            }}
          >
            {({ isSubmitting }) => (
              <Form>
                <TextFormField
                  name="google_primary_admin"
                  label="Primary Admin Email:"
                  subtext="You must provide an admin/owner account to retrieve all org emails."
                />
                <div className="flex">
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className={
                      "bg-slate-500 hover:bg-slate-700 text-white " +
                      "font-bold py-2 px-4 rounded focus:outline-none " +
                      "focus:shadow-outline w-full max-w-sm mx-auto"
                    }
                  >
                    Submit
                  </button>
                </div>
              </Form>
            )}
          </Formik>
        </CardSection>
      </div>
    );
  }

  if (appCredentialData?.client_id) {
    return (
      <div className="text-sm mb-4">
        <p className="mb-2">
          Next, you must provide credentials via OAuth. This gives us read
          access to the docs you have access to in your gmail account.
        </p>
        <Button
          onClick={async () => {
            const [authUrl, errorMsg] = await setupGmailOAuth({
              isAdmin: true,
            });
            if (authUrl) {
              // cookie used by callback to determine where to finally redirect to
              Cookies.set(GMAIL_AUTH_IS_ADMIN_COOKIE_NAME, "true", {
                path: "/",
              });
              router.push(authUrl);
              return;
            }

            setPopup({
              message: errorMsg,
              type: "error",
            });
          }}
        >
          Authenticate with Gmail
        </Button>
      </div>
    );
  }

  // case where no keys have been uploaded in step 1
  return (
    <p className="text-sm">
      Please upload an OAuth or Service Account Credential JSON in Step 1 before
      moving onto Step 2.
    </p>
  );
};
