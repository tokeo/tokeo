# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc
import warnings

from google.protobuf import empty_pb2 as google_dot_protobuf_dot_empty__pb2
from tokeo.core.grpc.proto import tokeo_pb2 as tokeo_dot_core_dot_grpc_dot_proto_dot_tokeo__pb2

GRPC_GENERATED_VERSION = '1.70.0'
GRPC_VERSION = grpc.__version__
_version_not_supported = False

try:
    from grpc._utilities import first_version_is_lower

    _version_not_supported = first_version_is_lower(GRPC_VERSION, GRPC_GENERATED_VERSION)
except ImportError:
    _version_not_supported = True

if _version_not_supported:
    raise RuntimeError(
        f'The grpc package installed is at version {GRPC_VERSION},'
        + f' but the generated code in tokeo/core/grpc/proto/tokeo_pb2_grpc.py depends on'
        + f' grpcio>={GRPC_GENERATED_VERSION}.'
        + f' Please upgrade your grpc module to grpcio>={GRPC_GENERATED_VERSION}'
        + f' or downgrade your generated code using grpcio-tools<={GRPC_VERSION}.'
    )


class TokeoStub(object):
    """The grpc service on Tokeo"""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.CountWords = channel.unary_unary(
            '/tokeo.Tokeo/CountWords',
            request_serializer=tokeo_dot_core_dot_grpc_dot_proto_dot_tokeo__pb2.CountWordsRequest.SerializeToString,
            response_deserializer=google_dot_protobuf_dot_empty__pb2.Empty.FromString,
            _registered_method=True,
        )


class TokeoServicer(object):
    """The grpc service on Tokeo"""

    def CountWords(self, request, context):
        """Sends a greeting"""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_TokeoServicer_to_server(servicer, server):
    rpc_method_handlers = {
        'CountWords': grpc.unary_unary_rpc_method_handler(
            servicer.CountWords,
            request_deserializer=tokeo_dot_core_dot_grpc_dot_proto_dot_tokeo__pb2.CountWordsRequest.FromString,
            response_serializer=google_dot_protobuf_dot_empty__pb2.Empty.SerializeToString,
        ),
    }
    generic_handler = grpc.method_handlers_generic_handler('tokeo.Tokeo', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))
    server.add_registered_method_handlers('tokeo.Tokeo', rpc_method_handlers)


# This class is part of an EXPERIMENTAL API.
class Tokeo(object):
    """The grpc service on Tokeo"""

    @staticmethod
    def CountWords(
        request,
        target,
        options=(),
        channel_credentials=None,
        call_credentials=None,
        insecure=False,
        compression=None,
        wait_for_ready=None,
        timeout=None,
        metadata=None,
    ):
        return grpc.experimental.unary_unary(
            request,
            target,
            '/tokeo.Tokeo/CountWords',
            tokeo_dot_core_dot_grpc_dot_proto_dot_tokeo__pb2.CountWordsRequest.SerializeToString,
            google_dot_protobuf_dot_empty__pb2.Empty.FromString,
            options,
            channel_credentials,
            insecure,
            call_credentials,
            compression,
            wait_for_ready,
            timeout,
            metadata,
            _registered_method=True,
        )
