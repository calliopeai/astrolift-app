import { gql } from "@apollo/client";

// fileUpload mints a pre-signed PUT URL; the browser uploads the bytes
// directly to object storage and then surfaces publicUrl to the caller
// so a runtime (job container, exec session) can fetch the artifact.
export const UPLOAD_FILE = gql`
  mutation UploadFile($mimetype: String!, $name: String) {
    fileUpload(mimetype: $mimetype, name: $name, isPublic: false) {
      id
      preSignedUrl
      publicUrl
    }
  }
`;
