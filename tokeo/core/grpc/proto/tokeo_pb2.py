# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# NO CHECKED-IN PROTOBUF GENCODE
# source: tokeo/core/grpc/proto/tokeo.proto
# Protobuf Python Version: 5.29.0
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import runtime_version as _runtime_version
from google.protobuf import symbol_database as _symbol_database
from google.protobuf.internal import builder as _builder
_runtime_version.ValidateProtobufRuntimeVersion(
    _runtime_version.Domain.PUBLIC,
    5,
    29,
    0,
    '',
    'tokeo/core/grpc/proto/tokeo.proto'
)
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


from google.protobuf import empty_pb2 as google_dot_protobuf_dot_empty__pb2


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n!tokeo/core/grpc/proto/tokeo.proto\x12\x05tokeo\x1a\x1bgoogle/protobuf/empty.proto\" \n\x11\x43ountWordsRequest\x12\x0b\n\x03url\x18\x01 \x01(\t2I\n\x05Tokeo\x12@\n\nCountWords\x12\x18.tokeo.CountWordsRequest\x1a\x16.google.protobuf.Empty\"\x00\x62\x06proto3')

_globals = globals()
_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, _globals)
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'tokeo.core.grpc.proto.tokeo_pb2', _globals)
if not _descriptor._USE_C_DESCRIPTORS:
  DESCRIPTOR._loaded_options = None
  _globals['_COUNTWORDSREQUEST']._serialized_start=73
  _globals['_COUNTWORDSREQUEST']._serialized_end=105
  _globals['_TOKEO']._serialized_start=107
  _globals['_TOKEO']._serialized_end=180
# @@protoc_insertion_point(module_scope)
