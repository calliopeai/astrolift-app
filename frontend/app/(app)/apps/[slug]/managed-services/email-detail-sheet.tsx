"use client";

/**
 * Email-service detail sheet (#629, #631, #632, #633, #634).
 *
 * Renders the SES (or future GCP/Azure) observability surface for one
 * bound email managed-service. Five panels:
 *
 *   1. Sender reputation / sandbox vs production (#633)
 *   2. Send-rate vs quota tile (#629)
 *   3. Identity verification badge with DKIM token list (#634)
 *   4. DKIM / SPF / DMARC DNS auth status (#632)
 *   5. Suppression list view + manage (#631)
 *
 * Backends that don't expose any of these (GCP, Azure) populate
 * `unsupportedNotes`; the UI shades the corresponding panel and shows
 * the upstream's hint instead of the data.
 *
 * The sheet renders inside the managed-services page so operators stay
 * in flow when debugging deliverability — no extra navigation.
 */

import { useMutation, useQuery } from "@apollo/client/react";
import {
	AlertTriangleIcon,
	CheckCircle2Icon,
	CopyIcon,
	GaugeIcon,
	HelpCircleIcon,
	Loader2Icon,
	ShieldIcon,
	ShieldXIcon,
	TrashIcon,
	XCircleIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetFooter,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
	ADD_EMAIL_SUPPRESSION_ENTRY,
	REMOVE_EMAIL_SUPPRESSION_ENTRY,
} from "@/graphql/services/services.mutations";
import { GET_EMAIL_SERVICE_DETAIL } from "@/graphql/services/services.queries";
import type {
	AstroliftEmailDnsAuthCheck,
	AstroliftEmailServiceDetail,
	AstroliftEmailSuppressionEntry,
	EmailDnsCheckOutcome,
} from "@/graphql/services/services.types";

interface DetailResp {
	astroliftEmailServiceDetail: AstroliftEmailServiceDetail | null;
}

interface AddResp {
	addEmailSuppressionEntry: MutationResult<{ address: string; reason: string }>;
}

interface RemoveResp {
	removeEmailSuppressionEntry: MutationResult<{
		address: string;
		removed: boolean;
	}>;
}

const OUTCOME_DOT: Record<EmailDnsCheckOutcome, string> = {
	GREEN: "bg-green-500",
	YELLOW: "bg-amber-500",
	RED: "bg-red-500",
	UNKNOWN: "bg-muted-foreground",
};

const OUTCOME_LABEL: Record<EmailDnsCheckOutcome, string> = {
	GREEN: "Healthy",
	YELLOW: "Suboptimal",
	RED: "Broken",
	UNKNOWN: "Unknown",
};

function copyToClipboard(value: string, label = "Value") {
	void navigator.clipboard.writeText(value).then(
		() => toast.success(`${label} copied`),
		() => toast.error("Copy failed"),
	);
}

function formatPct(value: number | null | undefined, digits = 2): string {
	if (value === null || value === undefined) return "—";
	return `${value.toFixed(digits)}%`;
}

function formatNumber(value: number, digits = 0): string {
	if (!Number.isFinite(value)) return "—";
	return value.toLocaleString("en-US", {
		minimumFractionDigits: digits,
		maximumFractionDigits: digits,
	});
}

interface EmailDetailSheetProps {
	managedServiceId: string;
	serviceName: string;
	open: boolean;
	onOpenChange: (next: boolean) => void;
}

export function EmailDetailSheet({
	managedServiceId,
	serviceName,
	open,
	onOpenChange,
}: EmailDetailSheetProps) {
	const { data, loading, refetch } = useQuery<DetailResp>(
		GET_EMAIL_SERVICE_DETAIL,
		{
			variables: { managedServiceId },
			skip: !open,
			fetchPolicy: "cache-and-network",
		},
	);
	const detail = data?.astroliftEmailServiceDetail ?? null;

	return (
		<Sheet open={open} onOpenChange={onOpenChange}>
			<SheetContent className="w-full sm:max-w-3xl overflow-y-auto">
				<SheetHeader>
					<SheetTitle>Email service: {serviceName}</SheetTitle>
					<SheetDescription>
						Deliverability health, identity verification, and suppression list
						for this bound email backend.
					</SheetDescription>
				</SheetHeader>

				{loading && !detail ? (
					<div className="space-y-4 p-1">
						<Skeleton className="h-24 w-full" />
						<Skeleton className="h-32 w-full" />
						<Skeleton className="h-48 w-full" />
					</div>
				) : detail ? (
					<div className="space-y-6 p-1">
						<ReputationPanel detail={detail} />
						<QuotaPanel detail={detail} />
						<IdentityPanel detail={detail} />
						<DnsAuthPanel detail={detail} />
						<SuppressionPanel
							detail={detail}
							onRefetch={() => {
								void refetch();
							}}
						/>
						{detail.unsupportedNotes.length > 0 ? (
							<Card className="bg-muted/40 p-3">
								<p className="text-muted-foreground mb-1 text-[11px] font-semibold uppercase">
									Notes from {detail.pluginSlug.toUpperCase()}
								</p>
								<ul className="text-muted-foreground space-y-0.5 text-xs">
									{detail.unsupportedNotes.map((note) => (
										<li key={note} className="font-mono">
											• {note}
										</li>
									))}
								</ul>
							</Card>
						) : null}
					</div>
				) : (
					<p className="text-muted-foreground p-3 text-sm">
						Email detail unavailable. This managed service may have been
						deprovisioned, or the cloud driver isn&apos;t reachable.
					</p>
				)}

				<SheetFooter className="mt-6">
					<Button variant="outline" onClick={() => onOpenChange(false)}>
						Close
					</Button>
				</SheetFooter>
			</SheetContent>
		</Sheet>
	);
}

// ── Reputation / sandbox status (#633) ─────────────────────────────

function ReputationPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
	const status = detail.accountStatus;
	if (!status) {
		return (
			<PanelEmpty
				title="Sender reputation"
				message="The backend doesn't expose reputation / sandbox status."
				icon={<ShieldXIcon className="size-4" />}
			/>
		);
	}

	const repPct =
		status.reputationScore !== null ? status.reputationScore * 100 : null;
	const repColor = (() => {
		if (repPct === null) return "bg-muted-foreground";
		if (repPct >= 70) return "bg-green-500";
		if (repPct >= 50) return "bg-amber-500";
		return "bg-red-500";
	})();

	return (
		<Card className="p-4">
			<div className="mb-3 flex items-center justify-between">
				<h3 className="flex items-center gap-2 text-sm font-semibold">
					<ShieldIcon className="size-4" />
					Sender reputation
				</h3>
				<Badge
					variant={status.productionAccess ? "default" : "outline"}
					className={
						status.productionAccess
							? "bg-green-500/10 text-green-700 dark:text-green-400"
							: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400"
					}
				>
					{status.productionAccess ? "Production access" : "Sandbox"}
				</Badge>
			</div>
			<div className="grid grid-cols-3 gap-3">
				<div>
					<p className="text-muted-foreground text-[11px] uppercase">
						Reputation
					</p>
					<p className="font-mono text-lg">
						{repPct === null ? "—" : `${repPct.toFixed(0)}%`}
					</p>
					{repPct !== null ? (
						<div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
							<div
								className={`h-full ${repColor}`}
								style={{ width: `${Math.min(100, Math.max(0, repPct))}%` }}
							/>
						</div>
					) : null}
				</div>
				<div>
					<p className="text-muted-foreground text-[11px] uppercase">
						Bounce rate (7d)
					</p>
					<p className="font-mono text-lg">{formatPct(status.bounceRatePct)}</p>
					<p className="text-muted-foreground text-[10px]">
						SES throttles at ~10%
					</p>
				</div>
				<div>
					<p className="text-muted-foreground text-[11px] uppercase">
						Complaint rate (7d)
					</p>
					<p className="font-mono text-lg">
						{formatPct(status.complaintRatePct)}
					</p>
					<p className="text-muted-foreground text-[10px]">
						SES throttles at ~0.5%
					</p>
				</div>
			</div>
			{!status.productionAccess ? (
				<p className="text-muted-foreground mt-3 flex items-start gap-1.5 text-xs">
					<AlertTriangleIcon className="size-3.5 shrink-0 text-amber-500" />
					Sandbox mode: this identity can only send to verified recipients +
					verified domains. Request production access from AWS Support before
					shipping to end users.
				</p>
			) : null}
		</Card>
	);
}

// ── Quota tile (#629) ──────────────────────────────────────────────

function QuotaPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
	const quota = detail.quota;
	if (!quota) {
		return (
			<PanelEmpty
				title="Send quota"
				message="The backend doesn't expose send-rate quota."
				icon={<GaugeIcon className="size-4" />}
			/>
		);
	}
	const dailyPct =
		quota.max24HourSend > 0
			? (quota.sentLast24h / quota.max24HourSend) * 100
			: 0;
	const dailyOverThreshold = dailyPct > 80;
	return (
		<Card className="p-4">
			<h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
				<GaugeIcon className="size-4" />
				Send quota
			</h3>
			<div className="grid grid-cols-2 gap-4">
				<div>
					<p className="text-muted-foreground text-[11px] uppercase">
						Max send rate
					</p>
					<p className="font-mono text-lg">
						{formatNumber(quota.maxSendRate, 1)} /sec
					</p>
				</div>
				<div>
					<p className="text-muted-foreground text-[11px] uppercase">
						Sent today
					</p>
					<p className="font-mono text-lg">
						{formatNumber(quota.sentLast24h)} /{" "}
						<span className="text-muted-foreground text-base">
							{formatNumber(quota.max24HourSend)}
						</span>
					</p>
					<div className="mt-2">
						<div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
							<div
								className={`h-full ${
									dailyOverThreshold ? "bg-amber-500" : "bg-primary"
								}`}
								style={{ width: `${Math.min(100, dailyPct)}%` }}
							/>
						</div>
						<p
							className={`mt-1 text-[10px] ${
								dailyOverThreshold ? "text-amber-600" : "text-muted-foreground"
							}`}
						>
							{dailyPct.toFixed(1)}% of 24h quota
							{dailyOverThreshold ? " — close to the daily ceiling" : ""}
						</p>
					</div>
				</div>
			</div>
		</Card>
	);
}

// ── Identity verification badge (#634) ─────────────────────────────

function IdentityPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
	const iv = detail.identityVerification;
	if (!iv) {
		return (
			<PanelEmpty
				title="Identity verification"
				message="The backend doesn't expose identity-verification details."
				icon={<HelpCircleIcon className="size-4" />}
			/>
		);
	}

	const verified = iv.status === "Success";
	const failed = iv.status === "Failed";
	return (
		<Card className="p-4">
			<div className="mb-3 flex items-center justify-between">
				<h3 className="flex items-center gap-2 text-sm font-semibold">
					Identity verification
				</h3>
				<Badge
					className={
						verified
							? "bg-green-500/10 text-green-700 dark:text-green-400"
							: failed
								? "bg-red-500/10 text-red-700 dark:text-red-400"
								: "bg-amber-500/10 text-amber-700 dark:text-amber-400"
					}
					variant="outline"
				>
					{verified ? (
						<CheckCircle2Icon className="mr-1 size-3" />
					) : failed ? (
						<XCircleIcon className="mr-1 size-3" />
					) : (
						<Loader2Icon className="mr-1 size-3 animate-spin" />
					)}
					{iv.status}
				</Badge>
			</div>
			<p className="text-muted-foreground mb-2 text-xs">
				<span className="text-foreground font-mono">{iv.identity}</span> (
				{iv.isDomain ? "domain identity" : "email identity"})
			</p>
			{iv.verificationToken ? (
				<div className="bg-muted/40 mb-3 rounded-md border p-2 font-mono text-[11px]">
					<p className="text-muted-foreground">TXT verification challenge:</p>
					<div className="mt-0.5 flex items-center gap-2">
						<code className="flex-1 break-all">{iv.verificationToken}</code>
						<Button
							variant="ghost"
							size="icon"
							className="size-6"
							onClick={() => copyToClipboard(iv.verificationToken, "Token")}
						>
							<CopyIcon className="size-3" />
						</Button>
					</div>
				</div>
			) : null}
			{iv.dkimTokens.length > 0 ? (
				<div className="space-y-1.5">
					<p className="text-muted-foreground text-[11px] uppercase">
						DKIM CNAMEs to publish
					</p>
					{iv.dkimTokens.map((tok) => (
						<div
							key={tok.token}
							className="bg-muted/40 flex items-center gap-2 rounded-md border p-2 font-mono text-[11px]"
						>
							<div className="min-w-0 flex-1">
								<p className="truncate text-foreground">{tok.cnameHost}</p>
								<p className="text-muted-foreground truncate">
									CNAME → {tok.cnameTarget}
								</p>
							</div>
							<Button
								variant="ghost"
								size="icon"
								className="size-6 shrink-0"
								onClick={() =>
									copyToClipboard(
										`${tok.cnameHost} CNAME ${tok.cnameTarget}`,
										"CNAME",
									)
								}
							>
								<CopyIcon className="size-3" />
							</Button>
						</div>
					))}
				</div>
			) : null}
		</Card>
	);
}

// ── DNS auth panel (#632) ──────────────────────────────────────────

function DnsAuthPanel({ detail }: { detail: AstroliftEmailServiceDetail }) {
	const dns = detail.dnsAuthStatus;
	if (!dns) {
		return (
			<PanelEmpty
				title="DKIM / SPF / DMARC"
				message="The backend doesn't expose DNS auth probing for this identity."
				icon={<ShieldIcon className="size-4" />}
			/>
		);
	}
	return (
		<Card className="p-4">
			<div className="mb-3 flex items-center justify-between">
				<h3 className="flex items-center gap-2 text-sm font-semibold">
					DKIM / SPF / DMARC
				</h3>
				<Badge variant="outline" className="flex items-center gap-1.5">
					<span
						className={`inline-block size-2 rounded-full ${OUTCOME_DOT[dns.overall]}`}
					/>
					{OUTCOME_LABEL[dns.overall]}
				</Badge>
			</div>
			<div className="space-y-2">
				<DnsCheckRow check={dns.dkim} />
				<DnsCheckRow check={dns.spf} />
				<DnsCheckRow check={dns.dmarc} />
			</div>
			<p className="text-muted-foreground mt-3 text-[10px]">
				Probed {new Date(dns.checkedAt).toLocaleString()}
			</p>
		</Card>
	);
}

function DnsCheckRow({ check }: { check: AstroliftEmailDnsAuthCheck }) {
	return (
		<div className="flex items-start gap-3 rounded-md border p-2">
			<span
				className={`mt-1.5 inline-block size-2 shrink-0 rounded-full ${OUTCOME_DOT[check.outcome]}`}
			/>
			<div className="min-w-0 flex-1">
				<p className="text-sm font-medium">
					{check.protocol}{" "}
					<span className="text-muted-foreground text-[10px]">
						{OUTCOME_LABEL[check.outcome]}
					</span>
				</p>
				{check.message ? (
					<p className="text-muted-foreground text-xs">{check.message}</p>
				) : null}
				{check.records.length > 0 ? (
					<details className="mt-1">
						<summary className="text-muted-foreground cursor-pointer text-[10px] uppercase">
							{check.records.length} record{check.records.length > 1 ? "s" : ""}
						</summary>
						<ul className="mt-1 space-y-0.5">
							{check.records.map((r) => (
								<li
									key={r}
									className="bg-muted/40 rounded p-1.5 font-mono text-[10px] break-all"
								>
									{r}
								</li>
							))}
						</ul>
					</details>
				) : null}
			</div>
		</div>
	);
}

// ── Suppression list (#631) ────────────────────────────────────────

function SuppressionPanel({
	detail,
	onRefetch,
}: {
	detail: AstroliftEmailServiceDetail;
	onRefetch: () => void;
}) {
	const [addAddress, setAddAddress] = React.useState("");
	const [addNote, setAddNote] = React.useState("");
	const [removalTarget, setRemovalTarget] =
		React.useState<AstroliftEmailSuppressionEntry | null>(null);

	const [addEntry, { loading: adding }] = useMutation<AddResp>(
		ADD_EMAIL_SUPPRESSION_ENTRY,
		{
			refetchQueries: [
				{
					query: GET_EMAIL_SERVICE_DETAIL,
					variables: { managedServiceId: detail.managedServiceId },
				},
			],
			awaitRefetchQueries: true,
		},
	);

	const [removeEntry, { loading: removing }] = useMutation<RemoveResp>(
		REMOVE_EMAIL_SUPPRESSION_ENTRY,
		{
			refetchQueries: [
				{
					query: GET_EMAIL_SERVICE_DETAIL,
					variables: { managedServiceId: detail.managedServiceId },
				},
			],
			awaitRefetchQueries: true,
		},
	);

	async function handleAdd() {
		const address = addAddress.trim();
		if (!address) return;
		try {
			const { data } = await addEntry({
				variables: {
					input: {
						managedServiceId: detail.managedServiceId,
						address,
						reason: "MANUAL",
						note: addNote.trim(),
					},
				},
			});
			const env = data?.addEmailSuppressionEntry;
			if (!env) {
				toast.error("No response from server");
				return;
			}
			if (!env.ok) {
				toast.error(env.errors[0]?.message ?? "Suppression add failed");
				return;
			}
			toast.success(`Suppressed ${address}`);
			setAddAddress("");
			setAddNote("");
			onRefetch();
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Suppression add failed",
			);
		}
	}

	async function handleRemove(entry: AstroliftEmailSuppressionEntry) {
		try {
			const { data } = await removeEntry({
				variables: {
					input: {
						managedServiceId: detail.managedServiceId,
						address: entry.address,
					},
				},
			});
			const env = data?.removeEmailSuppressionEntry;
			if (!env) {
				toast.error("No response from server");
				return;
			}
			if (!env.ok) {
				toast.error(env.errors[0]?.message ?? "Suppression remove failed");
				return;
			}
			if (env.data?.removed) {
				toast.success(`Removed ${entry.address} from suppression list`);
			} else {
				toast.info(`${entry.address} was already removed`);
			}
			setRemovalTarget(null);
			onRefetch();
		} catch (err) {
			toast.error(
				err instanceof Error ? err.message : "Suppression remove failed",
			);
		}
	}

	const isUnsupported = detail.unsupportedNotes.some((n) =>
		n.startsWith("suppression_entries"),
	);

	if (isUnsupported) {
		return (
			<PanelEmpty
				title="Suppression list"
				message="The backend doesn't expose an account-level suppression list."
				icon={<TrashIcon className="size-4" />}
			/>
		);
	}

	return (
		<Card className="p-4">
			<h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
				Suppression list
				<Badge variant="secondary" className="text-[10px]">
					{detail.suppressionEntries.length}
				</Badge>
			</h3>

			<Can permission="managed_service.update">
				<div className="bg-muted/30 mb-3 rounded-md border p-3">
					<p className="text-muted-foreground mb-2 text-[11px] uppercase">
						Add manual entry
					</p>
					<div className="space-y-2">
						<div>
							<Label htmlFor="suppress-address" className="text-xs">
								Address
							</Label>
							<Input
								id="suppress-address"
								value={addAddress}
								onChange={(e) => setAddAddress(e.target.value)}
								placeholder="recipient@example.com"
								className="font-mono text-xs"
								disabled={adding}
							/>
						</div>
						<div>
							<Label htmlFor="suppress-note" className="text-xs">
								Note (audit trail)
							</Label>
							<Input
								id="suppress-note"
								value={addNote}
								onChange={(e) => setAddNote(e.target.value)}
								placeholder="e.g. support ticket #4567"
								className="text-xs"
								disabled={adding}
							/>
						</div>
						<Button
							size="sm"
							onClick={handleAdd}
							disabled={adding || !addAddress.trim()}
							className="w-full"
						>
							{adding ? (
								<Loader2Icon className="mr-1.5 size-3 animate-spin" />
							) : null}
							Suppress address
						</Button>
					</div>
				</div>
			</Can>

			{detail.suppressionEntries.length === 0 ? (
				<p className="text-muted-foreground py-4 text-center text-xs">
					No suppressed addresses.
				</p>
			) : (
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>Address</TableHead>
							<TableHead>Reason</TableHead>
							<TableHead>Date</TableHead>
							<TableHead className="w-12 text-right"></TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{detail.suppressionEntries.map((entry) => (
							<TableRow key={entry.address}>
								<TableCell className="font-mono text-xs">
									{entry.address}
								</TableCell>
								<TableCell>
									<Badge variant="outline" className="text-[10px]">
										{entry.reason}
									</Badge>
								</TableCell>
								<TableCell className="text-muted-foreground text-xs">
									{new Date(entry.suppressedAt).toLocaleDateString()}
								</TableCell>
								<TableCell className="text-right">
									<Can permission="managed_service.update">
										<Tooltip>
											<TooltipTrigger asChild>
												<Button
													variant="ghost"
													size="icon"
													className="size-7"
													onClick={() => setRemovalTarget(entry)}
													disabled={removing}
												>
													<TrashIcon className="size-3.5" />
													<span className="sr-only">
														Remove from suppression
													</span>
												</Button>
											</TooltipTrigger>
											<TooltipContent>
												Un-suppress (allow sends again)
											</TooltipContent>
										</Tooltip>
									</Can>
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			)}

			<AlertDialog
				open={removalTarget !== null}
				onOpenChange={(next) => {
					if (!next) setRemovalTarget(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Un-suppress {removalTarget?.address}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Removes this address from the SES account-level suppression list.
							SES will accept future sends to this recipient. If the original
							suppression was a legitimate hard bounce, re-suppressing after
							another failure may damage sender reputation.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel disabled={removing}>Cancel</AlertDialogCancel>
						<AlertDialogAction
							disabled={removing}
							onClick={() => {
								if (removalTarget) {
									void handleRemove(removalTarget);
								}
							}}
						>
							{removing ? (
								<Loader2Icon className="mr-1.5 size-3 animate-spin" />
							) : null}
							Un-suppress
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</Card>
	);
}

// ── Empty-state cell ─────────────────────────────────────────────

function PanelEmpty({
	title,
	message,
	icon,
}: {
	title: string;
	message: string;
	icon: React.ReactNode;
}) {
	return (
		<Card className="bg-muted/30 p-4">
			<div className="text-muted-foreground flex items-start gap-2">
				<span className="mt-0.5">{icon}</span>
				<div>
					<p className="text-foreground text-sm font-semibold">{title}</p>
					<p className="text-xs">{message}</p>
				</div>
			</div>
		</Card>
	);
}
