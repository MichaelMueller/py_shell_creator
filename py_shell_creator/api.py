# built-in imports
import socket, json, sys, argparse, os, typing, inspect, gzip, io
from typing import Dict, Union, List, Tuple, Any, Annotated
from abc import abstractclassmethod

# pip imports
import jsonschema
from flask import Flask, request, jsonify, make_response

# basic type definitions
SerializableType = Union[None, str, int, bool, float, list, dict]

# functions
def create_schema_object_from_annotation( annotation:type ) -> Tuple[dict, bool]:
    schema_object = {}
    required = False
        
    if hasattr(annotation, "__annotations__"):  
        annotation = annotation.__annotations__

    if isinstance( annotation, dict ):      
        if len(annotation.keys()) > 0:
            schema_object["type"] = "object"
            schema_object["properties"] = {}
            schema_object["required"] = []
            for key, child_annotation in annotation.items():
                #print(key)
                schema_object["properties"][key], required = create_schema_object_from_annotation( child_annotation )
                if required:
                    schema_object["required"].append( key )

    else:
        type_dict_ = { str: "string", int: "number", float: "number", bool: "boolean" }

        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if origin == typing.Annotated:
            type_ = args[0]
            if type_ in type_dict_:
                schema_object["type"] = type_dict_[ args[0] ]
            else:
                schema_object, required = create_schema_object_from_annotation( args[0] )
            annotation_dict:dict = args[1]         
            for key, value in annotation_dict.items():
                if key == "required":
                    required = bool(value)
                else:
                    schema_object[key] = value    
                    if key == "default":
                        required = False               
        elif origin == typing.Literal:
            schema_object["enum"] = []
            for arg in args:
                schema_object["enum"].append( arg )
            required = True
        elif origin == typing.Union:
            schema_object["type"] = []
            for arg in args:
                if arg == type(None):
                    required = False
                elif arg in type_dict_:
                    schema_object["type"].append(  type_dict_[ arg ] )
                else:
                    schema_object, required = create_schema_object_from_annotation( arg )        
            if len(schema_object["type"]) == 1:
                schema_object["type"] = schema_object["type"][0]
        elif origin == list:
            type_ = args[0]
            schema_object["type"] = "array"
            schema_object["items"] = {}
            schema_object["items"], required = create_schema_object_from_annotation( args[0] )
        elif annotation in type_dict_:
            schema_object["type"] = type_dict_[ annotation ]
            required = True
        else:
            raise ValueError('Cannot create schema object for object "{}" with type "{}"'.format(annotation, type(annotation)))
        
    return (schema_object, required)

class ShellFunctionDescriptor:
    def __init__(self, function:callable, description:str, validate_schema:bool=True, command_line_function:bool=True) -> None:
        self.function = function
        self.description = description
        self.validate_schema = validate_schema
        self.command_line_function = command_line_function
        # protected
        self._args_json_schema:dict = None
        self._return_value_json_schema:dict = None

    def has_args( self ) -> bool:
        return "required" in self.get_args_json_schema() and len( self.get_args_json_schema()["required"] ) > 0
        
    def get_args_json_schema( self ) -> Union[dict, None]:
        if self._args_json_schema is None:
            signature_parameters = inspect.signature( self.function ).parameters
            params = dict( signature_parameters )
            default_values = {}
            for name in params.keys():
                #print(f"params[{name}]: {params[name]}")
                params[name] = params[name].annotation

                if signature_parameters[name].default is not inspect.Parameter.empty:
                    #print(f"default_values[{name}]: {parameters[name].default}")
                    default_values[name] = signature_parameters[name].default
            self._args_json_schema = create_schema_object_from_annotation( params )[0]
            for key, default_value in default_values.items():
                self._args_json_schema["properties"][key]["default"] = default_value
                if key in self._args_json_schema["required"]:
                    self._args_json_schema["required"].remove(key)
        
        #print(self._args_json_schema)
        return self._args_json_schema

    def get_return_value_json_schema( self ) -> Union[dict, None]:
        if self._return_value_json_schema is None:
            return_annotation = inspect.signature( self.function ).return_annotation
            self._return_value_json_schema = create_schema_object_from_annotation( return_annotation )[0]
        return self._return_value_json_schema

class Shell:
    def __init__(self) -> None:
        self._functions:Dict[str, ShellFunctionDescriptor] = {}

    def register( self, name:str, function:callable, description:str, validate_schema:bool=True, command_line_function:bool=True ) -> None:
        if name in self._functions:
            raise ValueError(f"Function with name {name} already registered")
        self._functions[name] = ShellFunctionDescriptor( function, description, validate_schema, command_line_function )
    
    def get_function_descriptor( self, name:str ) -> ShellFunctionDescriptor:
        return self._functions[name]
    
    def get_function_names(self) -> List[str]:
        return self._functions.keys()
    
    def get_non_command_line_function_names(self) -> List[str]:
        keys = []
        for key, function_descriptor in self._functions.items():
            if function_descriptor.command_line_function == False:
                keys.append( key )
        return keys

class CommandLineApp:
    def __init__(self, app_name, shell:Shell) -> None:
        self._app_name = app_name
        self._shell = shell

    def exec( self ) -> None:        
        parser = argparse.ArgumentParser(description=self._app_name)        
        #parser.add_argument( "function_name", type=str, required=True, choices=self._shell() )
        sub_parser = parser.add_subparsers(dest='function_name', required=True)
    
        for function_name in self._shell.get_function_names():
            function_descriptor = self._shell.get_function_descriptor( function_name )            
            function_parser = sub_parser.add_parser(function_name, help=function_descriptor.description)
            args_json_schema = function_descriptor.get_args_json_schema()

            def json_or_json_file(string):
                try:
                    if os.path.exists(string):
                        value = json.load( open( string ) )
                    else:
                        value = json.loads( string )
                except:
                    raise argparse.ArgumentTypeError(f"Not valid json or a not valid json file")
            
                if function_descriptor.validate_schema:                            
                    try:
                        jsonschema.validate(instance=value, schema=args_json_schema)
                    except Exception as e:                          
                        raise argparse.ArgumentTypeError("{}".format(e))                        
                return value
            
            function_parser.add_argument( ("" if function_descriptor.has_args() else "--" )+"args", type=json_or_json_file, help="json or json file with following schema: "+json.dumps(args_json_schema, indent=2) )

        args = vars( parser.parse_args() )
        function_descriptor = self._shell.get_function_descriptor( args["function_name"] )
        
        function_args = args["args"]
        result = function_descriptor.function( **function_args ) if function_args is not None else function_descriptor.function()

        if result is not None:
            print( json.dumps(result, indent=2) )
        sys.exit(0)


class WebService:
    def __init__(self, app_name:str, shell:Shell, port:int=7070, debug:bool=True) -> None:
        self._app_name = app_name
        self._shell = shell
        self._port = port
        self._debug = debug
        # state
        self._flask_app:Flask = None

    def flask_app( self ) -> Flask:  
        if self._flask_app is None:
            app = Flask( self._app_name)

            for function_name in self._shell.get_non_command_line_function_names():
                function_descriptor = self._shell.get_function_descriptor(function_name)

                class FlaskEndPoint:
                    def __init__(self, function_descriptor:ShellFunctionDescriptor) -> None:
                        self._function_descriptor = function_descriptor

                    def flask_end_point( self ):
                        function_descriptor = self._function_descriptor
                        
                        compression = False
                        if (request.headers['Content-Type'] == 'application/x-gzip'):
                            compression = True
                            compressed_data = io.BytesIO(request.data)
                            text_data = gzip.GzipFile(fileobj=compressed_data, mode='r').read()
                            json_args = json.loads( text_data )
                        else:
                            json_args = request.json

                        if function_descriptor.validate_schema:
                            jsonschema.validate( instance=json_args, schema=function_descriptor.get_args_json_schema() )

                        result = function_descriptor.function(**json_args) if function_descriptor.has_args() else function_descriptor.function()

                        if compression:
                            content = gzip.compress(json.dumps(result).encode('utf8'))
                            response = make_response(content)
                            response.headers['Content-length'] = len(content)
                            response.headers['Content-Encoding'] = 'gzip'
                            return response
                        else:
                            return jsonify( result )

                fep = FlaskEndPoint( function_descriptor )

                app.add_url_rule( "/{}".format(function_name), function_name, fep.flask_end_point, methods=["GET", "POST"] )
            
            self._flask_app = app
        return self._flask_app

    def exec( self ) -> None:        
        host = '0.0.0.0' if self._debug else None
        self.flask_app().run( debug=self._debug, host=host, port=self._port )

# class TcpService:
#     def __init__(self, host:str, port:int, shell:shell, max_buffer_size:int=2048) -> None:
#         self._host = host
#         self._port = port
#         self._shell = shell
#         self._max_buffer_size = max_buffer_size
    
#     def exec( self ) -> None:
#         # form https://stackoverflow.com/questions/53348412/sending-json-object-to-a-tcp-listener-port-in-use-python
#         data = ""
#         with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
#             s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#             s.bind((self._host,self._port))
#             s.listen()
#             while 1: # Accept connections from multiple clients
#                 print('Listening for client...')
#                 conn, addr = s.accept()
#                 print('Connection address:', addr)
#                 while 1: # Accept multiple messages from each client
#                     buffer = conn.recv(self._max_buffer_size)
#                     buffer = buffer.decode()
#                     data_tuple = json.loads( buffer.strip() )
#                     function_name = data_tuple[0]
#                     inputs = data_tuple[1]
#                     result = self._shell.exec( function_name, inputs )  
#                     conn.sendall( json.dumps( result ).encode() )
#                     conn.close()