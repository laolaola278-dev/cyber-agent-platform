import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ListError } from "../components/ListError";

// The console used to render an empty table for every failed fetch. These guard
// the fix: a failure must read as a failure, and must be retryable.
describe("ListError", () => {
  it("renders nothing when there is no error", () => {
    const { container } = render(<ListError error={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the server message and a retry control", async () => {
    const onRetry = vi.fn();
    render(
      <ListError error="数据库连接超时" onRetry={onRetry} description="事件列表" />,
    );

    expect(await screen.findByText("数据加载失败")).toBeInTheDocument();
    expect(screen.getByText(/数据库连接超时/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("omits the retry control when the caller cannot retry", () => {
    render(<ListError error="boom" />);
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });
});
