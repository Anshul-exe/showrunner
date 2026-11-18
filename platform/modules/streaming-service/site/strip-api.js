// CloudFront Function (viewer request) on /api/*: the HTTP API's routes have no /api
// prefix, so /api/titles/t01 -> /titles/t01. Query strings pass through unchanged.
function handler(event) {
  var request = event.request;
  request.uri = request.uri.replace(/^\/api(?=\/|$)/, "") || "/";
  return request;
}
