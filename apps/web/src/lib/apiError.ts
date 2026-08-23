/**
 * The error every API call raises.
 *
 * Split into its own module so the preview adapter can raise the same type
 * without importing the live client, which would import the adapter back.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
