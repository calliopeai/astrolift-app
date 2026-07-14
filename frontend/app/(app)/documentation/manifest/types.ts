export interface ManifestField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

export interface ManifestSection {
  key: string;
  title: string;
  summary: string;
  required: boolean;
  fields: ManifestField[];
}

export interface ManifestSchema {
  version: string;
  sections: ManifestSection[];
  _comment?: string;
}
