import { Dispatch, SetStateAction, useEffect, useRef } from "react";
import { ModalWrapper } from "@/components/modals/ModalWrapper";
import { Text } from "@tremor/react";
import { getDisplayNameForModel, LlmOverride } from "@/lib/hooks";
import { LLMProviderDescriptor } from "@/app/admin/configuration/llm/interfaces";

import { destructureValue, structureValue } from "@/lib/llm/utils";
import { setUserDefaultModel } from "@/lib/users/UserSettings";
import { useRouter } from "next/navigation";
import { PopupSpec } from "@/components/admin/connectors/Popup";
import { useUser } from "@/components/user/UserProvider";

export function SetDefaultModelModal({
  setPopup,
  llmProviders,
  onClose,
  setLlmOverride,
  defaultModel,
}: {
  setPopup: (popupSpec: PopupSpec | null) => void;
  llmProviders: LLMProviderDescriptor[];
  setLlmOverride: Dispatch<SetStateAction<LlmOverride>>;
  onClose: () => void;
  defaultModel: string | null;
}) {
  const { refreshUser } = useUser();
  const containerRef = useRef<HTMLDivElement>(null);
  const messageRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    const message = messageRef.current;

    if (container && message) {
      const checkScrollable = () => {
        if (container.scrollHeight > container.clientHeight) {
          message.style.display = "block";
        } else {
          message.style.display = "none";
        }
      };
      checkScrollable();
      window.addEventListener("resize", checkScrollable);
      return () => window.removeEventListener("resize", checkScrollable);
    }
  }, []);

  const defaultModelDestructured = defaultModel
    ? destructureValue(defaultModel)
    : null;
  const modelOptionsByProvider = new Map<
    string,
    { name: string; value: string }[]
  >();
  llmProviders.forEach((llmProvider) => {
    const providerOptions = llmProvider.model_names.map(
      (modelName: string) => ({
        name: getDisplayNameForModel(modelName),
        value: modelName,
      })
    );
    modelOptionsByProvider.set(llmProvider.name, providerOptions);
  });

  const llmOptionsByProvider: {
    [provider: string]: { name: string; value: string }[];
  } = {};
  const uniqueModelNames = new Set<string>();

  llmProviders.forEach((llmProvider) => {
    if (!llmOptionsByProvider[llmProvider.provider]) {
      llmOptionsByProvider[llmProvider.provider] = [];
    }

    (llmProvider.display_model_names || llmProvider.model_names).forEach(
      (modelName) => {
        if (!uniqueModelNames.has(modelName)) {
          uniqueModelNames.add(modelName);
          llmOptionsByProvider[llmProvider.provider].push({
            name: modelName,
            value: structureValue(
              llmProvider.name,
              llmProvider.provider,
              modelName
            ),
          });
        }
      }
    );
  });

  const llmOptions = Object.entries(llmOptionsByProvider).flatMap(
    ([provider, options]) => [...options]
  );

  const router = useRouter();
  const handleChangedefaultModel = async (defaultModel: string | null) => {
    try {
      const response = await setUserDefaultModel(defaultModel);

      if (response.ok) {
        if (defaultModel) {
          setLlmOverride(destructureValue(defaultModel));
        }
        setPopup({
          message: "Default model updated successfully",
          type: "success",
        });
        refreshUser();
        router.refresh();
      } else {
        throw new Error("Failed to update default model");
      }
    } catch (error) {
      setPopup({
        message: "Failed to update default model",
        type: "error",
      });
    }
  };
  const defaultProvider = llmProviders.find(
    (llmProvider) => llmProvider.is_default_provider
  );

  return (
    <ModalWrapper
      onClose={onClose}
      modalClassName="rounded-lg  bg-white max-w-xl"
    >
      <>
        <div className="flex mb-4">
          <h2 className="text-2xl text-emphasis font-bold flex my-auto">
            Set Default Model
          </h2>
        </div>

        <Text className="mb-4">
          Choose a Large Language Model (LLM) to serve as the default for
          assistants that don&apos;t have a default model assigned.
          {defaultModel == null && "  No default model has been selected!"}
        </Text>
        <div
          className="w-full max-h-96 overflow-y-auto flex text-sm flex-col border rounded-md"
          ref={containerRef}
        >
          <div
            ref={messageRef}
            className="sticky top-0 bg-background-100 p-2 text-xs text-emphasis font-medium"
            style={{ display: "none" }}
          >
            Scroll to see all options
          </div>
          <div>
            <div
              key={-1}
              className="w-full border-b flex items-center gap-x-2 hover:bg-background-50"
            >
              <input
                checked={defaultModelDestructured?.modelName == null}
                type="radio"
                name="credentialSelection"
                onChange={(e) => {
                  e.preventDefault();
                  handleChangedefaultModel(null);
                }}
                className="form-radio ml-4 h-4 w-4 text-blue-600 transition duration-150 ease-in-out"
              />
              {
                <td className="p-2">
                  System default{" "}
                  {defaultProvider?.default_model_name &&
                    `(${getDisplayNameForModel(defaultProvider?.default_model_name)})`}
                </td>
              }
            </div>

            {llmOptions.map(({ name, value }, index) => {
              return (
                <div
                  key={index}
                  className="w-full flex items-center gap-x-2 border-b hover:bg-background-50"
                >
                  <input
                    checked={defaultModelDestructured?.modelName == name}
                    type="radio"
                    name="credentialSelection"
                    onChange={(e) => {
                      e.preventDefault();
                      handleChangedefaultModel(value);
                    }}
                    className="form-radio ml-4 h-4 w-4 text-blue-600 transition duration-150 ease-in-out"
                  />

                  <td className="p-2">
                    {getDisplayNameForModel(name)}{" "}
                    {defaultModelDestructured &&
                      defaultModelDestructured.name == name &&
                      "(selected)"}
                  </td>
                </div>
              );
            })}
          </div>
        </div>
      </>
    </ModalWrapper>
  );
}
