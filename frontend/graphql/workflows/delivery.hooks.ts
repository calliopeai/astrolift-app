"use client";

import { useMutation, useQuery } from "@apollo/client/react";

import type {
  CreateWorkflowDeliveryChannelMutation,
  CreateWorkflowDeliveryChannelMutationVariables,
  DeleteWorkflowDeliveryChannelMutation,
  DeleteWorkflowDeliveryChannelMutationVariables,
  SetWorkflowDeliveryChannelsMutation,
  SetWorkflowDeliveryChannelsMutationVariables,
  UpdateWorkflowDeliveryChannelMutation,
  UpdateWorkflowDeliveryChannelMutationVariables,
  WorkflowDeliveryChannelsQuery,
  WorkflowDeliverySelectionQuery,
  WorkflowDeliverySelectionQueryVariables,
} from "@/graphql/__generated__/operations";

import {
  CREATE_WORKFLOW_DELIVERY_CHANNEL,
  DELETE_WORKFLOW_DELIVERY_CHANNEL,
  SET_WORKFLOW_DELIVERY_CHANNELS,
  UPDATE_WORKFLOW_DELIVERY_CHANNEL,
} from "./delivery.mutations";
import { WORKFLOW_DELIVERY_CHANNELS, WORKFLOW_DELIVERY_SELECTION } from "./delivery.queries";

// Result delivery channels for scheduled workflows (#2244).

export const useWorkflowDeliveryChannels = (skip = false) =>
  useQuery<WorkflowDeliveryChannelsQuery>(WORKFLOW_DELIVERY_CHANNELS, { skip });

export const useWorkflowDeliverySelection = (workflowId: string | null) =>
  useQuery<WorkflowDeliverySelectionQuery, WorkflowDeliverySelectionQueryVariables>(
    WORKFLOW_DELIVERY_SELECTION,
    { variables: { workflowId: workflowId ?? "" }, skip: !workflowId }
  );

export const useCreateWorkflowDeliveryChannel = () =>
  useMutation<
    CreateWorkflowDeliveryChannelMutation,
    CreateWorkflowDeliveryChannelMutationVariables
  >(CREATE_WORKFLOW_DELIVERY_CHANNEL, { refetchQueries: [WORKFLOW_DELIVERY_CHANNELS] });

export const useUpdateWorkflowDeliveryChannel = () =>
  useMutation<
    UpdateWorkflowDeliveryChannelMutation,
    UpdateWorkflowDeliveryChannelMutationVariables
  >(UPDATE_WORKFLOW_DELIVERY_CHANNEL, { refetchQueries: [WORKFLOW_DELIVERY_CHANNELS] });

export const useDeleteWorkflowDeliveryChannel = () =>
  useMutation<
    DeleteWorkflowDeliveryChannelMutation,
    DeleteWorkflowDeliveryChannelMutationVariables
  >(DELETE_WORKFLOW_DELIVERY_CHANNEL, { refetchQueries: [WORKFLOW_DELIVERY_CHANNELS] });

export const useSetWorkflowDeliveryChannels = () =>
  useMutation<SetWorkflowDeliveryChannelsMutation, SetWorkflowDeliveryChannelsMutationVariables>(
    SET_WORKFLOW_DELIVERY_CHANNELS,
    { refetchQueries: [WORKFLOW_DELIVERY_SELECTION] }
  );
