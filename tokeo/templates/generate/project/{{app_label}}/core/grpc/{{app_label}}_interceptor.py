"""
gRPC server interceptor(s) for the {{ app_class_name }} service.

Interceptors run as a cross-cutting layer in front of every rpc, before the
request reaches the servicer. They are the place for auth, logging or tracing.
The tokeo grpc extension wires them in via the grpc_interceptors config
(module:method), which is resolved to the {{ app_label }}_interceptor
factory below.

### Notes:

- This shipped version is a pass-through stub that allows every call; fill in
    the real checks where the TODO marks it
- mTLS (when enabled) already verified the client certificate during the tls
    handshake, so an interceptor only ever sees admitted peers; per request
    authorization (tokens, scopes, the client identity) is decided here
- Interceptors run sequentially in the order returned by the factory, each
    deciding whether to call continuation (pass on) or to abort the call

"""
import grpc


class AuthInterceptor(grpc.ServerInterceptor):
    """
    Server interceptor that gates every rpc before it reaches the servicer.

    The shipped implementation is a pass-through: it allows all calls. To
    enforce authentication, inspect the request metadata or the peer identity
    and abort unauthorized calls instead of continuing.

    ### Notes:

    : To allow a call, return continuation(handler_call_details); to reject
        one, return an rpc handler that aborts with an UNAUTHENTICATED or
        PERMISSION_DENIED status instead

    : The mTLS client identity is available on the per-call rpc context
        (context.auth_context / peer_identities), not on handler_call_details

    """

    def intercept_service(self, continuation, handler_call_details):
        """
        Intercept an incoming rpc and decide whether it proceeds.

        ### Args:

        - **continuation** (callable): Calls the next interceptor or the
            actual rpc handler; invoke it to let the call proceed
        - **handler_call_details** (grpc.HandlerCallDetails): The invoked
            method name and the request invocation_metadata

        ### Returns:

        - **grpc.RpcMethodHandler**: The handler that will serve the call

        ### Notes:

        : The default stub forwards every call unchanged; real checks read
            handler_call_details.invocation_metadata and reject when needed

        """
        # TODO: read handler_call_details.invocation_metadata, validate the
        # token and abort with grpc.StatusCode.UNAUTHENTICATED if invalid.
        # default stub: allow everything
        return continuation(handler_call_details)


def {{ app_label }}_interceptor():
    """
    Factory for the gRPC server interceptor chain.

    Referenced by the grpc_interceptors config (module:method) and called once
    while the server is built. Return the interceptors in the order they should
    run; the first one wraps the outermost layer.

    ### Returns:

    - **list**: The grpc.ServerInterceptor instances to install, in order

    ### Notes:

    : The tokeo grpc extension also accepts a single interceptor here, but a
        list keeps the order explicit and lets the chain grow

    """
    return [AuthInterceptor()]
