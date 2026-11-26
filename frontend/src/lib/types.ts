import type { components } from "./api-schema";

type S = components["schemas"];
export type Me = S["MeOut"];
export type Role = S["Role"];
export type User = S["UserOut"];
export type UserCreate = S["UserCreate"];
export type Device = S["DeviceOut"];
export type Platform = S["Platform"];
export type PlatformInfo = S["PlatformOut"];
export type DeviceCreate = S["DeviceCreate"];
export type DeviceStatus = S["DeviceStatus"];
export type DeviceGroup = S["DeviceGroupOut"];
export type Credential = S["CredentialOut"];
export type CredentialCreate = S["CredentialCreate"];
export type Job = S["JobOut"];
export type JobDetail = S["JobDetailOut"];
export type JobType = S["JobTypeOut"];
export type JobCreate = S["JobCreate"];
export type JobAccepted = S["JobAccepted"];
export type Alert = S["AlertOut"];
export type AlertRule = S["AlertRuleOut"];
export type Backup = S["BackupOut"];
export type BackupDetail = S["BackupDetailOut"];
export type Diff = S["DiffOut"];
export type Drift = S["DriftOut"];
export type RestoreResponse = S["RestoreResponse"];
export type Endpoint = S["EndpointOut"];
export type EndpointUpdate = S["EndpointUpdate"];
export type NacSession = S["SessionOut"];
export type Policy = S["PolicyOut"];
export type PolicyIn = S["PolicyIn"];
export type PolicyVersion = S["PolicyVersionOut"];
export type Decision = S["DecisionOut"];
export type SimulateIn = S["SimulateIn"];
export type Audit = S["AuditOut"];
export type Interface = S["InterfaceOut"];
export type Vlan = S["VlanOut"];
export type ChangeResponse = S["ChangeResponse"];
export type ExecuteResponse = S["ExecuteCommandResponse"];
export type TestConnection = S["TestConnectionResponse"];
export type MetricPoint = S["MetricPoint"];
export type HostKey = S["HostKeyOut"];
export type MacEntry = S["MacEntryOut"];
export type MacLocation = S["MacLocationOut"];
export type Arp = S["ArpOut"];
export type Finding = S["FindingOut"];
export type RadiusServer = S["RadiusServerOut"];
export type RadiusServerIn = S["RadiusServerIn"];
export type Route = S["RouteOut"];
export type Stp = S["StpStatusOut"];
export type CoaOut = S["CoaOut"];
export type ProvisionIn = S["ProvisionIn"];
export type ProvisionPreview = S["ProvisionPreviewOut"];

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Problem = {
  type?: string;
  title?: string;
  status: number;
  detail?: string;
  code?: string;
  message?: string;
  request_id?: string | null;
  details?: unknown;
};

/** Fields shared by every guarded configuration change. */
export type Guard = {
  dry_run?: boolean;
  confirm?: boolean;
  confirmation_token?: string | null;
  reason?: string | null;
  save?: boolean;
  override_lockout?: boolean;
  rollback_minutes?: number | null;
};
