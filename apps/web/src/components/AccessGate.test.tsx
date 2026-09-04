import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/apiError";

import { AccessGate } from "./AccessGate";

const sessionStatus = vi.fn();
const signIn = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: {
      sessionStatus: () => sessionStatus(),
      signIn: (password: string) => signIn(password),
    },
  };
});

function renderGate() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccessGate>
        <p>the war room</p>
      </AccessGate>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStatus.mockReset();
  signIn.mockReset();
});

describe("AccessGate", () => {
  it("shows the app when no password is configured", async () => {
    // Local development must not grow a login screen.
    sessionStatus.mockResolvedValue({ required: false, authenticated: true });
    renderGate();

    expect(await screen.findByText("the war room")).toBeInTheDocument();
    expect(screen.queryByLabelText(/shared password/i)).not.toBeInTheDocument();
  });

  it("shows the app when already signed in", async () => {
    sessionStatus.mockResolvedValue({ required: true, authenticated: true });
    renderGate();

    expect(await screen.findByText("the war room")).toBeInTheDocument();
  });

  it("asks for the password when the instance is gated", async () => {
    sessionStatus.mockResolvedValue({ required: true, authenticated: false });
    renderGate();

    expect(await screen.findByLabelText(/shared password/i)).toBeInTheDocument();
    expect(screen.queryByText("the war room")).not.toBeInTheDocument();
  });

  it("does not flash the form at somebody who is already signed in", async () => {
    // A login screen appearing for a moment reads as having been logged out,
    // which during a draft is alarming and wrong.
    let resolve: (value: unknown) => void = () => {};
    sessionStatus.mockReturnValue(new Promise((r) => (resolve = r)));
    renderGate();

    expect(screen.queryByLabelText(/shared password/i)).not.toBeInTheDocument();

    resolve({ required: true, authenticated: true });
    expect(await screen.findByText("the war room")).toBeInTheDocument();
  });

  it("lets the app through when the API cannot be reached at all", async () => {
    // An unreachable backend is not a locked one. A password form here would
    // send somebody hunting for a password that would not have helped.
    sessionStatus.mockRejectedValue(new ApiError("offline", 0));
    renderGate();

    expect(await screen.findByText("the war room")).toBeInTheDocument();
  });

  it("opens the app once the right password is accepted", async () => {
    sessionStatus.mockResolvedValue({ required: true, authenticated: false });
    signIn.mockImplementation(async () => {
      sessionStatus.mockResolvedValue({ required: true, authenticated: true });
      return { required: true, authenticated: true };
    });
    renderGate();

    await userEvent.type(await screen.findByLabelText(/shared password/i), "letmein");
    await userEvent.click(screen.getByRole("button", { name: /enter/i }));

    expect(await screen.findByText("the war room")).toBeInTheDocument();
    expect(signIn).toHaveBeenCalledWith("letmein");
  });

  it("says so when the password is wrong", async () => {
    sessionStatus.mockResolvedValue({ required: true, authenticated: false });
    signIn.mockRejectedValue(new ApiError("nope", 401));
    renderGate();

    await userEvent.type(await screen.findByLabelText(/shared password/i), "guess");
    await userEvent.click(screen.getByRole("button", { name: /enter/i }));

    await waitFor(() =>
      expect(screen.getByText(/that password is not right/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText("the war room")).not.toBeInTheDocument();
  });

  it("distinguishes being locked out from guessing wrong", async () => {
    // Telling somebody their password is wrong when the real problem is a
    // lockout sends them to look up a password they already had right.
    sessionStatus.mockResolvedValue({ required: true, authenticated: false });
    signIn.mockRejectedValue(new ApiError("slow down", 429));
    renderGate();

    await userEvent.type(await screen.findByLabelText(/shared password/i), "guess");
    await userEvent.click(screen.getByRole("button", { name: /enter/i }));

    await waitFor(() =>
      expect(screen.getByText(/too many attempts/i)).toBeInTheDocument(),
    );
  });

  it("does not submit an empty password", async () => {
    sessionStatus.mockResolvedValue({ required: true, authenticated: false });
    renderGate();

    await screen.findByLabelText(/shared password/i);
    expect(screen.getByRole("button", { name: /enter/i })).toBeDisabled();
  });
});
