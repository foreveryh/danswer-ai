"use client";
import React, {
  createContext,
  useState,
  useContext,
  useMemo,
  useEffect,
} from "react";
import { Persona } from "@/app/admin/assistants/interfaces";
import {
  classifyAssistants,
  orderAssistantsForUser,
  getUserCreatedAssistants,
} from "@/lib/assistants/utils";
import { useUser } from "../user/UserProvider";

interface AssistantsContextProps {
  assistants: Persona[];
  visibleAssistants: Persona[];
  hiddenAssistants: Persona[];
  finalAssistants: Persona[];
  ownedButHiddenAssistants: Persona[];
  refreshAssistants: () => Promise<void>;
  isImageGenerationAvailable: boolean;
  recentAssistants: Persona[];
  refreshRecentAssistants: (currentAssistant: number) => Promise<void>;
  // Admin only
  editablePersonas: Persona[];
  allAssistants: Persona[];
}

const AssistantsContext = createContext<AssistantsContextProps | undefined>(
  undefined
);

export const AssistantsProvider: React.FC<{
  children: React.ReactNode;
  initialAssistants: Persona[];
  hasAnyConnectors: boolean;
  hasImageCompatibleModel: boolean;
}> = ({
  children,
  initialAssistants,
  hasAnyConnectors,
  hasImageCompatibleModel,
}) => {
  const [assistants, setAssistants] = useState<Persona[]>(
    initialAssistants || []
  );
  const { user, isLoadingUser, isAdmin } = useUser();
  const [editablePersonas, setEditablePersonas] = useState<Persona[]>([]);
  const [allAssistants, setAllAssistants] = useState<Persona[]>([]);

  const [recentAssistants, setRecentAssistants] = useState<Persona[]>(
    user?.preferences.recent_assistants
      ?.filter((assistantId) =>
        assistants.find((assistant) => assistant.id === assistantId)
      )
      .map(
        (assistantId) =>
          assistants.find((assistant) => assistant.id === assistantId)!
      ) || []
  );

  const [isImageGenerationAvailable, setIsImageGenerationAvailable] =
    useState<boolean>(false);

  useEffect(() => {
    const checkImageGenerationAvailability = async () => {
      try {
        const response = await fetch("/api/persona/image-generation-tool");
        if (response.ok) {
          const { is_available } = await response.json();
          setIsImageGenerationAvailable(is_available);
        }
      } catch (error) {
        console.error("Error checking image generation availability:", error);
      }
    };

    checkImageGenerationAvailability();
  }, []);

  useEffect(() => {
    const fetchPersonas = async () => {
      if (!isAdmin) {
        return;
      }

      try {
        const [editableResponse, allResponse] = await Promise.all([
          fetch("/api/admin/persona?get_editable=true"),
          fetch("/api/admin/persona"),
        ]);

        if (editableResponse.ok) {
          const editablePersonas = await editableResponse.json();
          setEditablePersonas(editablePersonas);
        }

        if (allResponse.ok) {
          const allPersonas = await allResponse.json();
          setAllAssistants(allPersonas);
        }
      } catch (error) {
        console.error("Error fetching personas:", error);
      }
    };

    fetchPersonas();
  }, [isAdmin]);

  const refreshRecentAssistants = async (currentAssistant: number) => {
    const response = await fetch("/api/user/recent-assistants", {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        current_assistant: currentAssistant,
      }),
    });
    if (!response.ok) {
      return;
    }
    setRecentAssistants((recentAssistants) => [
      assistants.find((assistant) => assistant.id === currentAssistant)!,

      ...recentAssistants.filter(
        (assistant) => assistant.id !== currentAssistant
      ),
    ]);
  };

  const refreshAssistants = async () => {
    try {
      const response = await fetch("/api/persona", {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
        },
      });
      if (!response.ok) throw new Error("Failed to fetch assistants");
      let assistants: Persona[] = await response.json();
      if (!hasImageCompatibleModel) {
        assistants = assistants.filter(
          (assistant) =>
            !assistant.tools.some(
              (tool) => tool.in_code_tool_id === "ImageGenerationTool"
            )
        );
      }
      if (!hasAnyConnectors) {
        assistants = assistants.filter(
          (assistant) => assistant.num_chunks === 0
        );
      }
      setAssistants(assistants);
    } catch (error) {
      console.error("Error refreshing assistants:", error);
    }
    setRecentAssistants(
      assistants.filter(
        (assistant) =>
          user?.preferences.recent_assistants?.includes(assistant.id) || false
      )
    );
  };

  const {
    visibleAssistants,
    hiddenAssistants,
    finalAssistants,
    ownedButHiddenAssistants,
  } = useMemo(() => {
    const { visibleAssistants, hiddenAssistants } = classifyAssistants(
      user,
      assistants
    );

    const finalAssistants = user
      ? orderAssistantsForUser(visibleAssistants, user)
      : visibleAssistants;

    const ownedButHiddenAssistants = getUserCreatedAssistants(
      user,
      hiddenAssistants
    );

    return {
      visibleAssistants,
      hiddenAssistants,
      finalAssistants,
      ownedButHiddenAssistants,
    };
  }, [user, assistants, isLoadingUser]);

  return (
    <AssistantsContext.Provider
      value={{
        assistants,
        visibleAssistants,
        hiddenAssistants,
        finalAssistants,
        ownedButHiddenAssistants,
        refreshAssistants,
        editablePersonas,
        allAssistants,
        isImageGenerationAvailable,
        recentAssistants,
        refreshRecentAssistants,
      }}
    >
      {children}
    </AssistantsContext.Provider>
  );
};

export const useAssistants = (): AssistantsContextProps => {
  const context = useContext(AssistantsContext);
  if (!context) {
    throw new Error("useAssistants must be used within an AssistantsProvider");
  }
  return context;
};
