import copy
from typing import Optional, Any

import torch
from torch import nn, Tensor
import torch.nn.functional as F
from torch.nn import Module
from torch.nn import MultiheadAttention
from torch.nn import ModuleList
from torch.nn.init import xavier_uniform_
from torch.nn import Dropout


class Cattention(nn.Module):

    def __init__(self, in_dim):
        super(Cattention, self).__init__()
        self.chanel_in = in_dim
        self.conv1 = nn.Sequential(
            nn.ConvTranspose2d(in_dim * 2, in_dim, kernel_size=1, stride=1),
        )
        self.conv2 = nn.Sequential(
            nn.Conv2d(in_dim, in_dim, kernel_size=3, stride=3),
            nn.BatchNorm2d(in_dim),
            nn.ReLU(inplace=True),
        )
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.linear1 = nn.Conv2d(in_dim, in_dim // 6, 1, bias=False)
        self.linear2 = nn.Conv2d(in_dim // 6, in_dim, 1, bias=False)
        self.gamma = nn.Parameter(torch.zeros(1))
        self.activation = nn.ReLU(inplace=True)
        self.dropout = nn.Dropout()

    def forward(self, x, y):
        ww = self.linear2(self.dropout(self.activation(self.linear1(self.avg_pool(self.conv2(y))))))
        weight = self.conv1(torch.cat((x, y), 1)) * ww

        return x + self.gamma * weight * x


class Transformer_time_encoder(Module):

    def __init__(self, d_model: int = 512, nhead: int = 8, num_encoder_layers: int = 6, dim_feedforward: int = 384, dropout: float = 0.1,
                 activation: str = "relu", custom_encoder: Optional[Any] = None) -> None:
        super(Transformer_time_encoder, self).__init__()

        if custom_encoder is not None:
            self.encoder = custom_encoder
        else:
            encoder_layer = TransformerEncoderLayer(d_model, nhead, dim_feedforward, dropout, activation)
            # encoder_norm = nn.LayerNorm(d_model)
            self.encoder = TransformerEncoder(encoder_layer, num_encoder_layers, norm=None)

        self._reset_parameters()

        self.d_model = d_model
        self.nhead = nhead

    def forward(self, src: Tensor, srcc: Tensor, src_mask: Optional[Tensor] = None, src_key_padding_mask: Optional[Tensor] = None) -> Tensor:

        if src.size(1) != srcc.size(1):
            raise RuntimeError("the batch number of src and tgt must be equal")

        if src.size(2) != self.d_model or srcc.size(2) != self.d_model:
            raise RuntimeError("the feature number of src and tgt must be equal to d_model")

        memory = self.encoder(src, srcc, mask=src_mask, src_key_padding_mask=src_key_padding_mask)

        return memory

    def generate_square_subsequent_mask(self, sz: int) -> Tensor:
        r"""Generate a square mask for the sequence. The masked positions are filled with float('-inf').
            Unmasked positions are filled with float(0.0).
        """
        mask = (torch.triu(torch.ones(sz, sz)) == 1).transpose(0, 1)
        mask = mask.float().masked_fill(mask == 0, float('-inf')).masked_fill(mask == 1, float(0.0))
        return mask

    def _reset_parameters(self):
        r"""Initiate parameters in the transformer model."""

        for p in self.parameters():
            if p.dim() > 1:
                xavier_uniform_(p)


class TransformerEncoder(Module):
    r"""TransformerEncoder is a stack of N encoder layers

    Args:
        encoder_layer: an instance of the TransformerEncoderLayer() class (required).
        num_layers: the number of sub-encoder-layers in the encoder (required).
        norm: the layer normalization component (optional).

    Examples::
        >>> encoder_layer = nn.TransformerEncoderLayer(d_model=512, nhead=8)
        >>> transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=6)
        >>> src = torch.rand(10, 32, 512)
        >>> out = transformer_encoder(src)
    """
    __constants__ = ['norm']

    def __init__(self, encoder_layer, num_layers, norm=None):
        super(TransformerEncoder, self).__init__()
        self.layers = _get_clones(encoder_layer, num_layers)
        self.num_layers = num_layers
        self.norm = norm

    def forward(self, src: Tensor, srcc: Tensor, mask: Optional[Tensor] = None,
                src_key_padding_mask: Optional[Tensor] = None) -> Tensor:
        r"""Pass the input through the encoder layers in turn.

        Args:
            src: the sequence to the encoder (required).
            mask: the mask for the src sequence (optional).
            src_key_padding_mask: the mask for the src keys per batch (optional).

        Shape:
            see the docs in Transformer class.
        """
        output = src

        for mod in self.layers:
            output = mod(output, srcc, src_mask=mask, src_key_padding_mask=src_key_padding_mask)

        if self.norm is not None:
            output = self.norm(output)

        return output


class TransformerEncoderLayer(Module):
    r"""TransformerEncoderLayer is made up of self-attn and feedforward network.
    This standard encoder layer is based on the paper "Attention Is All You Need".
    Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N Gomez,
    Lukasz Kaiser, and Illia Polosukhin. 2017. Attention is all you need. In Advances in
    Neural Information Processing Systems, pages 6000-6010. Users may modify or implement
    in a different way during application.

    Args:
        d_model: the number of expected features in the input (required).
        nhead: the number of heads in the multiheadattention models (required).
        dim_feedforward: the dimension of the feedforward network model (default=384).
        dropout: the dropout value (default=0.1).
        activation: the activation function of intermediate layer, relu or gelu (default=relu).

    Examples::
        >>> encoder_layer = nn.TransformerEncoderLayer(d_model=512, nhead=8)
        >>> src = torch.rand(10, 32, 512)
        >>> out = encoder_layer(src)
    """

    def __init__(self, d_model, nhead, dim_feedforward=384, dropout=0.1, activation="relu"):
        super(TransformerEncoderLayer, self).__init__()
        self.self_attn1 = MultiheadAttention(d_model, nhead, dropout=dropout)
        self.self_attn2 = MultiheadAttention(d_model, nhead, dropout=dropout)
        self.self_attn3 = MultiheadAttention(d_model, nhead, dropout=dropout)
        channel = dim_feedforward // 2
        self.modulation = Cattention(channel)

        self.norm1 = nn.LayerNorm(d_model)

        self.dropout1 = Dropout(dropout)
        self.norm2 = nn.LayerNorm(d_model)
        self.dropout2 = Dropout(dropout)
        self.norm3 = nn.LayerNorm(d_model)
        self.dropout3 = Dropout(dropout)

        self.activation = _get_activation_fn(activation)

    def __setstate__(self, state):
        if 'activation' not in state:
            state['activation'] = F.relu
        super(TransformerEncoderLayer, self).__setstate__(state)

    def forward(self, src: Tensor, srcc: Tensor, src_mask: Optional[Tensor] = None,
                src_key_padding_mask: Optional[Tensor] = None) -> Tensor:
        b, c, s = src.permute(1, 2, 0).size()

        src1 = self.self_attn1(srcc, src, src, attn_mask=src_mask,
                               key_padding_mask=src_key_padding_mask)[0]
        srcs1 = src + self.dropout1(src1)
        srcs1 = self.norm1(srcs1)

        src2 = self.self_attn2(srcs1, srcs1, srcs1, attn_mask=src_mask,
                               key_padding_mask=src_key_padding_mask)[0]
        srcs2 = srcs1 + self.dropout2(src2)
        srcs2 = self.norm2(srcs2)

        src = self.modulation(srcs2.view(b, c, int(s ** 0.5), int(s ** 0.5)) \
                              , srcs1.contiguous().view(b, c, int(s ** 0.5), int(s ** 0.5))).view(b, c, -1).permute(2, 0, 1)

        src2 = self.self_attn3(src, src, src, attn_mask=src_mask,
                               key_padding_mask=src_key_padding_mask)[0]
        srcs1 = src + self.dropout3(src2)
        srcs1 = self.norm3(srcs1)

        return srcs1

def _get_clones(module, N):
    return ModuleList([copy.deepcopy(module) for i in range(N)])


def _get_activation_fn(activation):
    if activation == "relu":
        return F.relu
    elif activation == "gelu":
        return F.gelu

    raise RuntimeError("activation should be relu/gelu, not {}".format(activation))