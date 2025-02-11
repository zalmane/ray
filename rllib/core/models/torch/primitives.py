from typing import Callable, List, Optional, Union, Tuple

from ray.rllib.models.torch.misc import same_padding
from ray.rllib.models.utils import get_activation_fn
from ray.rllib.utils.framework import try_import_torch

torch, nn = try_import_torch()


class TorchMLP(nn.Module):
    """A multi-layer perceptron with N dense layers.

    All layers (except for an optional additional extra output layer) share the same
    activation function, bias setup (use bias or not), and LayerNorm setup
    (use layer normalization or not).

    If `output_dim` (int) is not None, an additional, extra output dense layer is added,
    which might have its own activation function (e.g. "linear"). However, the output
    layer does NOT use layer normalization.
    """

    def __init__(
        self,
        *,
        input_dim: int,
        hidden_layer_dims: List[int],
        hidden_layer_activation: Union[str, Callable] = "relu",
        hidden_layer_use_layernorm: bool = False,
        output_dim: Optional[int] = None,
        output_activation: Union[str, Callable] = "linear",
        use_bias: bool = True,
    ):
        """Initialize a TorchMLP object.

        Args:
            input_dim: The input dimension of the network. Must not be None.
            hidden_layer_dims: The sizes of the hidden layers. If an empty list, only a
                single layer will be built of size `output_dim`.
            hidden_layer_use_layernorm: Whether to insert a LayerNormalization
                functionality in between each hidden layer's output and its activation.
            hidden_layer_activation: The activation function to use after each layer
                (except for the output). Either a torch.nn.[activation fn] callable or
                the name thereof, or an RLlib recognized activation name,
                e.g. "ReLU", "relu", "tanh", "SiLU", or "linear".
            output_dim: The output dimension of the network. If None, no specific output
                layer will be added and the last layer in the stack will have
                size=`hidden_layer_dims[-1]`.
            output_activation: The activation function to use for the output layer
                (if any). Either a torch.nn.[activation fn] callable or
                the name thereof, or an RLlib recognized activation name,
                e.g. "ReLU", "relu", "tanh", "SiLU", or "linear".
            use_bias: Whether to use bias on all dense layers (including the possible
                output layer).
        """
        super().__init__()
        assert input_dim > 0

        self.input_dim = input_dim

        hidden_activation = get_activation_fn(
            hidden_layer_activation, framework="torch"
        )

        layers = []
        dims = (
            [self.input_dim] + hidden_layer_dims + ([output_dim] if output_dim else [])
        )
        for i in range(0, len(dims) - 1):
            layers.append(nn.Linear(dims[i], dims[i + 1], bias=use_bias))

            # We are still in the hidden layer section: Possibly add layernorm and
            # hidden activation.
            if output_dim is None or i < len(dims) - 2:
                # Insert a layer normalization in between layer's output and
                # the activation.
                if hidden_layer_use_layernorm:
                    layers.append(nn.LayerNorm(dims[i + 1]))
                # Add the activation function.
                if hidden_activation is not None:
                    layers.append(hidden_activation())

        # Add output layer's (if any) activation.
        output_activation = get_activation_fn(output_activation, framework="torch")
        if output_dim is not None and output_activation is not None:
            layers.append(output_activation())

        self.mlp = nn.Sequential(*layers)

        self.expected_input_dtype = torch.float32

    def forward(self, x):
        return self.mlp(x.type(self.expected_input_dtype))


class TorchCNN(nn.Module):
    """A model containing a CNN with N Conv2D layers.

    All layers share the same activation function, bias setup (use bias or not),
    and LayerNorm setup (use layer normalization or not).

    Note that there is no flattening nor an additional dense layer at the end of the
    stack. The output of the network is a 3D tensor of dimensions
    [width x height x num output filters].
    """

    def __init__(
        self,
        *,
        input_dims: Union[List[int], Tuple[int]],
        cnn_filter_specifiers: List[List[Union[int, List]]],
        cnn_use_layernorm: bool = False,
        cnn_activation: str = "relu",
        use_bias: bool = True,
    ):
        """Initializes a TorchCNN instance.

        Args:
            input_dims: The 3D input dimensions of the network (incoming image).
            cnn_filter_specifiers: A list of lists, where each item represents one
                Conv2D layer. Each such Conv2D layer is further specified by the
                elements of the inner lists. The inner lists follow the format:
                `[number of filters, kernel, stride]` to
                specify a convolutional layer stacked in order of the outer list.
                `kernel` as well as `stride` might be provided as width x height tuples
                OR as single ints representing both dimension (width and height)
                in case of square shapes.
            cnn_use_layernorm: Whether to insert a LayerNorm functionality
                in between each CNN layer's outputs and its activation.
            cnn_activation: The activation function to use after each Conv2D layer.
            use_bias: Whether to use bias on all Conv2D layers.
        """
        super().__init__()

        assert len(input_dims) == 3

        cnn_activation = get_activation_fn(cnn_activation, framework="torch")

        layers = []

        # Add user-specified hidden convolutional layers first
        width, height, in_depth = input_dims
        in_size = [width, height]
        for out_depth, kernel, stride in cnn_filter_specifiers:
            # Pad like in tensorflow's SAME mode.
            padding, out_size = same_padding(in_size, kernel, stride)
            layers.extend(
                [
                    nn.ZeroPad2d(padding),
                    nn.Conv2d(in_depth, out_depth, kernel, stride, bias=use_bias),
                ]
            )
            # Layernorm.
            if cnn_use_layernorm:
                layers.append(nn.LayerNorm((out_depth, out_size[0], out_size[1])))
            # Activation.
            if cnn_activation is not None:
                layers.append(cnn_activation())

            in_size = out_size
            in_depth = out_depth

        self.output_width, self.output_height = out_size
        self.output_depth = out_depth

        # Create the CNN.
        self.cnn = nn.Sequential(*layers)

        self.expected_input_dtype = torch.float32

    def forward(self, inputs):
        # Permute b/c data comes in as [B, dim, dim, channels]:
        inputs = inputs.permute(0, 3, 1, 2)
        return self.cnn(inputs.type(self.expected_input_dtype))
