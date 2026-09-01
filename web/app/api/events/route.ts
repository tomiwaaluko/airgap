import { loadSnapshot } from "@/lib/broker";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const POLL_MS = 1000;

export async function GET(request: Request): Promise<Response> {
  const encoder = new TextEncoder();
  let closed = false;
  const abort = () => {
    closed = true;
  };
  request.signal.addEventListener("abort", abort);

  const stream = new ReadableStream({
    async start(controller) {
      try {
        while (!closed) {
          const snapshot = await loadSnapshot();
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(snapshot)}\n\n`),
          );
          await wait(POLL_MS, () => closed);
        }
      } catch {
        closed = true;
      } finally {
        request.signal.removeEventListener("abort", abort);
        try {
          controller.close();
        } catch {
          return;
        }
      }
    },
    cancel() {
      closed = true;
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      Connection: "keep-alive",
    },
  });
}

function wait(ms: number, done: () => boolean): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    if (done()) {
      clearTimeout(timer);
      resolve();
    }
  });
}
