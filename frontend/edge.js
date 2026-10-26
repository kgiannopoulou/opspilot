// Container entrypoint: the Next.js standalone server, with this container acting as the edge.
//
// The API rate-limits and audits by client IP, and believes X-Forwarded-For only from this proxy
// (OPSPILOT_TRUSTED_PROXIES). Next.js rewrites pass request headers through but never add the client
// address, and a browser can send its own X-Forwarded-For. So before Next.js sees a request, any
// client-supplied value is replaced with the TCP peer address. Behind a load balancer that sets the
// header itself, start with OPSPILOT_KEEP_FORWARDED_FOR=1 and trust the balancer instead.
const http = require("node:http");

if (process.env.OPSPILOT_KEEP_FORWARDED_FOR !== "1") {
  const createServer = http.createServer;
  http.createServer = function (...args) {
    const server = createServer.apply(this, args);
    server.prependListener("request", (req) => {
      req.headers["x-forwarded-for"] = req.socket.remoteAddress ?? "";
    });
    return server;
  };
}

require("./server.js");
