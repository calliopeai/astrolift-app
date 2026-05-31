/** TypeScript types for the pipeline subsystem GraphQL surface (#64, #84, #90, #100). */

export interface PipelineSecret {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
}

export interface AstroliftPipeline {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  secrets: PipelineSecret[];
  createdAt: string;
  updatedAt: string;
}

export interface SetPipelineSecretData {
  pipelineId: string;
  name: string;
}

export interface DeletePipelineSecretData {
  pipelineId: string;
  name: string;
}
