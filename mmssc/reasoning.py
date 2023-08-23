import argparse
from typing import List

import cv2
import torch

from .dataset import get_transforms
from .model import Encoder, Decoder
from .Chemical_library import convert_graph_to_smiles
from .tokenizer import get_tokenizer


def safe_load(module, module_states):
    def remove_prefix(state_dict):
        return {k.replace('module.', ''): v for k, v in state_dict.items()}
    missing_keys, unexpected_keys = module.load_state_dict(remove_prefix(module_states), strict=False)
    return


class MMSSCNet:

    def __init__(self, model_path, device=None):

        args = self._get_args()
        model_states = torch.load(model_path, map_location=torch.device('cpu'))
        for key, value in model_states['args'].items():
            args.__dict__[key] = value
        if device is None:
            device = torch.device('cpu')
        self.device = device
        self.tokenizer = get_tokenizer(args)
        self.encoder, self.decoder = self._get_model(args, self.tokenizer, self.device, model_states)
        self.transform = get_transforms(args.input_size, augment=False)

    def _get_args(self):
        parser = argparse.ArgumentParser()
        # Model
        parser.add_argument('--encoder', type=str, default='swin_base')
        parser.add_argument('--decoder', type=str, default='encoder_decoder')
        parser.add_argument('--trunc_encoder', action='store_true')  # use the hidden states before downsample
        parser.add_argument('--no_pretrained', action='store_true')
        parser.add_argument('--use_checkpoint', action='store_true', default=True)
        parser.add_argument('--dropout', type=float, default=0.5)
        parser.add_argument('--embed_dim', type=int, default=256)
        parser.add_argument('--enc_pos_emb', action='store_true')
        group = parser.add_argument_group("transformer_options")
        group.add_argument("--dec_num_layers", help="No. of layers in encoder_decoder decoder", type=int, default=6)
        group.add_argument("--dec_hidden_size", help="Decoder hidden size", type=int, default=256)
        group.add_argument("--dec_attn_heads", help="Decoder no. of attention heads", type=int, default=8)
        group.add_argument("--dec_num_queries", type=int, default=128)
        group.add_argument("--hidden_dropout", help="Hidden dropout", type=float, default=0.1)
        group.add_argument("--attn_dropout", help="Attention dropout", type=float, default=0.1)
        group.add_argument("--max_relative_positions", help="Max relative positions", type=int, default=0)
        parser.add_argument('--continuous_coords', action='store_true')
        parser.add_argument('--compute_confidence', action='store_true')
        # Data
        parser.add_argument('--input_size', type=int, default=256)
        parser.add_argument('--vocab_file', type=str, default=None)
        parser.add_argument('--coord_bins', type=int, default=64)
        parser.add_argument('--sep_xy', action='store_true', default=True)

        args = parser.parse_args([])
        return args

    def _get_model(self, args, tokenizer, device, states):
        encoder = Encoder(args, pretrained=False)
        args.encoder_dim = encoder.n_features
        decoder = Decoder(args, tokenizer)

        safe_load(encoder, states['encoder'])
        safe_load(decoder, states['decoder'])
        # print(f"Model loaded from {load_path}")

        encoder.to(device)
        decoder.to(device)
        encoder.eval()
        decoder.eval()
        return encoder, decoder

    def predict_images(self, input_images: List, batch_size=16):
        device = self.device
        predictions = []

        for idx in range(0, len(input_images), batch_size):
            batch_images = input_images[idx:idx+batch_size]
            images = [self.transform(image=image, keypoints=[])['image'] for image in batch_images]
            images = torch.stack(images, dim=0).to(device)
            with torch.no_grad():
                features, hiddens = self.encoder(images)
                batch_predictions = self.decoder.decode(features, hiddens)
            predictions += batch_predictions

        smiles = [pred['chartok_coords']['smiles'] for pred in predictions]
        node_coords = [pred['chartok_coords']['coords'] for pred in predictions]
        node_symbols = [pred['chartok_coords']['symbols'] for pred in predictions]
        edges = [pred['edges'] for pred in predictions]

        smiles, molblock, r_success = convert_graph_to_smiles(node_coords, node_symbols, edges, images=input_images)
        return smiles, molblock

    def predict_image(self, image):
        smiles, molblock = self.predict_images([image])
        return smiles[0], molblock[0]

    def predict_image_files(self, image_files: List):
        input_images = []
        for path in image_files:
            image = cv2.imread(path)
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            input_images.append(image)
        return self.predict_images(input_images)

    def predict_image_file(self, image_file: str):
        smiles, molblock = self.predict_image_files([image_file])
        return smiles[0], molblock[0]
