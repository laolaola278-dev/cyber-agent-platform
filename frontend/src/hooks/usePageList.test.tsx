import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App as AntApp } from "antd";
import { describe, expect, it } from "vitest";
import { calls, resetCalls, stub } from "../test/httpMock";
import { usePageList } from "../hooks/usePageList";
import { ListError } from "../components/ListError";

/** A page-shaped probe: what every list page actually renders. */
function Probe({ path }: { path: string }) {
  const { rows, loading, error, pagination, refresh } = usePageList<{ name: string }>(path);
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="count">{rows.length}</span>
      <ListError error={error} onRetry={refresh} />
      <span data-testid="total">{pagination.total}</span>
    </div>
  );
}

const renderProbe = (path = "/incidents") => render(<AntApp><Probe path={path} /></AntApp>);

describe("usePageList", () => {
  it("loads a page, sends pagination params and reports the server total", async () => {
    resetCalls();
    stub({ "/incidents": { data: { items: [{ name: "a" }, { name: "b" }], page: 1, page_size: 20, total: 42 } } });

    renderProbe();

    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("2"));
    expect(screen.getByTestId("total")).toHaveTextContent("42");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
    expect(screen.queryByText("数据加载失败")).not.toBeInTheDocument();

    const [request] = calls("get");
    expect(request.url).toBe("/incidents");
    expect(request.config.params).toEqual({ page: 1, page_size: 20 });
  });

  it("accepts a bare array from endpoints that do not paginate", async () => {
    stub({ "/sandbox": { data: [{ name: "x" }] } });

    renderProbe("/sandbox");

    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("1"));
    expect(screen.getByTestId("total")).toHaveTextContent("1");
  });

  it("surfaces a server error instead of rendering it as an empty dataset", async () => {
    stub({ "/incidents": { status: 503, detail: "maintenance" } });

    renderProbe();

    // This is the defect that used to be invisible: no rows, no message.
    // The report is deliberately doubled (inline alert + toast), so each copy
    // is asserted in the region that owns it rather than on the whole screen.
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("数据加载失败")).toBeInTheDocument();
    // The backend `detail` must reach the operator, not just the HTTP status.
    expect(within(alert).getByText(/maintenance/)).toBeInTheDocument();
    // Awaited, not read synchronously: the inline alert arrives with the same
    // state update as `error`, while antd's App message API mounts the toast's
    // DOM on a later tick. Asserting it here without waiting passed only when
    // the awaits above happened to leave enough time -- which made CI red on a
    // commit that touched no frontend file. A toast that never renders still
    // fails this test; it just fails after findByText gives up.
    expect(await screen.findByText(/加载 \/incidents 失败：maintenance/)).toBeInTheDocument();
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("re-requests the path when the operator retries", async () => {
    resetCalls();
    let attempt = 0;
    stub({
      "/incidents": () => {
        attempt += 1;
        return attempt === 1
          ? { status: 500, detail: "transient" }
          : { data: { items: [{ name: "ok" }], page: 1, page_size: 20, total: 1 } };
      },
    });

    renderProbe();

    await screen.findByText("数据加载失败");
    await userEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(screen.getByTestId("count")).toHaveTextContent("1"));
    expect(screen.queryByText("数据加载失败")).not.toBeInTheDocument();
    expect(calls("get")).toHaveLength(2);
  });

  it("does not refetch on a re-render with unchanged inputs", async () => {
    resetCalls();
    stub({ "/assets": { data: { items: [], page: 1, page_size: 20, total: 0 } } });

    const { rerender } = render(<AntApp><Probe path="/assets" /></AntApp>);
    await screen.findByTestId("count");
    const before = calls("get").length;

    rerender(<AntApp><Probe path="/assets" /></AntApp>);
    expect(calls("get")).toHaveLength(before);
  });
});
