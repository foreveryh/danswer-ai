import { useField } from "formik";
import { FileUpload } from "@/components/admin/connectors/FileUpload";
import CredentialSubText from "@/components/credentials/CredentialFields";

interface FileInputProps {
  name: string;
  label: string;
  optional?: boolean;
  description?: string;
  isZip?: boolean;
}

export default function FileInput({
  name,
  label,
  optional = false,
  description,
  isZip = false, // Default to false for multiple file uploads
}: FileInputProps) {
  const [field, meta, helpers] = useField(name);

  return (
    <>
      <label
        htmlFor={name}
        className="block text-sm font-medium text-text-700 mb-1"
      >
        {label}
        {optional && <span className="text-text-500 ml-1">(optional)</span>}
      </label>
      {description && <CredentialSubText>{description}</CredentialSubText>}
      <FileUpload
        selectedFiles={
          Array.isArray(field.value)
            ? field.value
            : field.value
              ? [field.value]
              : []
        }
        setSelectedFiles={(files: File[]) => {
          if (isZip) {
            helpers.setValue(files[0] || null);
          } else {
            helpers.setValue(files);
          }
        }}
        multiple={!isZip} // Allow multiple files if not a zip
        accept={isZip ? ".zip" : undefined} // Only accept zip files if isZip is true
      />
      {meta.touched && meta.error && (
        <div className="text-red-500 text-sm mt-1">{meta.error}</div>
      )}
    </>
  );
}
