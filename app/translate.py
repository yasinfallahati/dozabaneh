MODEL_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
MODEL_FILES = ["model.bin", "shared_vocabulary.txt", "config.json", "tokenizer.json"]

NLLB_CODES = {
    "af": "afr_Latn", "am": "amh_Ethi", "ar": "arb_Arab", "as": "asm_Beng", "az": "azj_Latn",
    "ba": "bak_Cyrl", "be": "bel_Cyrl", "bg": "bul_Cyrl", "bn": "ben_Beng", "bo": "bod_Tibt",
    "bs": "bos_Latn", "ca": "cat_Latn", "ceb": "ceb_Latn", "ckb": "ckb_Arab", "cs": "ces_Latn",
    "cy": "cym_Latn", "da": "dan_Latn", "de": "deu_Latn", "el": "ell_Grek", "en": "eng_Latn",
    "eo": "epo_Latn", "es": "spa_Latn", "et": "est_Latn", "eu": "eus_Latn", "fa": "pes_Arab",
    "fi": "fin_Latn", "fo": "fao_Latn", "fr": "fra_Latn", "ga": "gle_Latn", "gd": "gla_Latn",
    "gl": "glg_Latn", "gn": "grn_Latn", "gu": "guj_Gujr", "ha": "hau_Latn", "he": "heb_Hebr",
    "hi": "hin_Deva", "hr": "hrv_Latn", "ht": "hat_Latn", "hu": "hun_Latn", "hy": "hye_Armn",
    "id": "ind_Latn", "ig": "ibo_Latn", "is": "isl_Latn", "it": "ita_Latn", "ja": "jpn_Jpan",
    "jv": "jav_Latn", "ka": "kat_Geor", "kk": "kaz_Cyrl", "km": "khm_Khmr", "kn": "kan_Knda",
    "ko": "kor_Hang", "ku": "kmr_Latn", "ky": "kir_Cyrl", "lb": "ltz_Latn", "ln": "lin_Latn",
    "lo": "lao_Laoo", "lt": "lit_Latn", "lv": "lvs_Latn", "mg": "plt_Latn", "mi": "mri_Latn",
    "mk": "mkd_Cyrl", "ml": "mal_Mlym", "mn": "khk_Cyrl", "mr": "mar_Deva", "ms": "zsm_Latn",
    "mt": "mlt_Latn", "my": "mya_Mymr", "ne": "npi_Deva", "nl": "nld_Latn", "nn": "nno_Latn",
    "no": "nob_Latn", "oc": "oci_Latn", "pa": "pan_Guru", "pl": "pol_Latn", "ps": "pbt_Arab",
    "pt": "por_Latn", "ro": "ron_Latn", "ru": "rus_Cyrl", "rw": "kin_Latn", "sa": "san_Deva",
    "sd": "snd_Arab", "si": "sin_Sinh", "sk": "slk_Latn", "sl": "slv_Latn", "sm": "smo_Latn",
    "sn": "sna_Latn", "so": "som_Latn", "sq": "als_Latn", "sr": "srp_Cyrl", "st": "sot_Latn",
    "su": "sun_Latn", "sv": "swe_Latn", "sw": "swh_Latn", "ta": "tam_Taml", "te": "tel_Telu",
    "tg": "tgk_Cyrl", "th": "tha_Thai", "tk": "tuk_Latn", "tl": "tgl_Latn", "tr": "tur_Latn",
    "tt": "tat_Cyrl", "ug": "uig_Arab", "uk": "ukr_Cyrl", "ur": "urd_Arab", "uz": "uzn_Latn",
    "vi": "vie_Latn", "wo": "wol_Latn", "xh": "xho_Latn", "yi": "ydd_Hebr", "yo": "yor_Latn",
    "yue": "yue_Hant", "zh": "zho_Hans", "zu": "zul_Latn",
}

WHISPER_ALIASES = {"jw": "jv"}


class TranslationError(Exception):
    pass


def canonical_language(code):
    return WHISPER_ALIASES.get(code, code)


def nllb_code(code):
    return NLLB_CODES.get(canonical_language(code))


def translate_lines(lines, send, batch_size=16, on_progress=None):
    results = [""] * len(lines)
    pending = [i for i, line in enumerate(lines) if line.strip()]
    for offset in range(0, len(pending), batch_size):
        indexes = pending[offset : offset + batch_size]
        try:
            translated = send([lines[i] for i in indexes])
        except Exception as error:
            raise TranslationError(str(error)) from error
        for index, text in zip(indexes, translated, strict=True):
            results[index] = text
        if on_progress:
            on_progress((offset + len(indexes)) / len(pending))
    if on_progress:
        on_progress(1.0)
    return results


class NllbTranslator:
    """Offline translation engine. The model is downloaded once, on first use."""

    def __init__(self):
        self._translator = None
        self._tokenizer = None

    def _load(self):
        if self._translator is None:
            import ctranslate2
            from huggingface_hub import snapshot_download
            from tokenizers import Tokenizer

            path = snapshot_download(MODEL_REPO, allow_patterns=MODEL_FILES)
            self._translator = ctranslate2.Translator(path, device="cpu", compute_type="int8")
            self._tokenizer = Tokenizer.from_file(f"{path}/tokenizer.json")

    def __call__(self, source, target):
        source_code, target_code = nllb_code(source), nllb_code(target)

        def send(lines):
            self._load()
            encoded = [
                [source_code, *self._tokenizer.encode(line, add_special_tokens=False).tokens, "</s>"]
                for line in lines
            ]
            results = self._translator.translate_batch(
                encoded,
                target_prefix=[[target_code]] * len(lines),
                beam_size=4,
                max_decoding_length=200,
                no_repeat_ngram_size=3,
            )
            return [self._decode(result.hypotheses[0][1:]) for result in results]

        return send

    def _decode(self, tokens):
        ids = [self._tokenizer.token_to_id(token) for token in tokens]
        return self._tokenizer.decode([i for i in ids if i is not None]).strip()
