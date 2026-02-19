# Copyright (c) 2024 Disintar LLP Licensed under the Apache License Version 2.0
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from loguru import logger

@dataclass
class Config:
    # Logging / output
    loglevel: int = 1
    color_schema_path: Optional[str] = None
    color_schema: Optional[Dict[str, Any]] = None

    # Emulators
    emulator_path: Optional[str] = None
    emulator_unchanged_path: Optional[str] = None
    c7_rewrite_raw: Optional[str] = None
    c7_rewrite: Optional[Dict[str, Any]] = None

    # Input filters
    txs_to_process_path: Optional[str] = None
    txs_to_process: Optional[Dict[str, Any]] = None

    # Liteserver (legacy single server - for backward compatibility)
    liteserver_ip: Optional[int] = None
    liteserver_port: Optional[int] = None
    liteserver_pubkey: Optional[str] = None
    liteserver_timeout: float = 5.0
    
    # Multiple liteservers (new feature)
    liteservers: List[Dict[str, Any]] = field(default_factory=list)

    to_seqno: Optional[int] = None
    from_seqno: Optional[int] = None
    to_emulate_mc_blocks: int = 10

    only_mc_blocks: bool = False
    parse_over_ls: bool = False

    # Performance
    nproc: int = 10
    chunk_size: int = 2
    tx_chunk_size: int = 40000

    # Toncenter / traces
    toncenter_api: str = "https://toncenter.com/api/v3"
    toncenter_api_key: Optional[str] = None
    toncenter_tx_hash: Optional[str] = None
    toncenter_msg_hash: Optional[str] = None
    toncenter_traces_by_masters: bool = False

    # Debug dumps for failed transactions
    debug_dumps_dir: Optional[str] = None

    def liteclient_server(self) -> Dict[str, Any]:
        """Legacy method for single server. Returns first server from list or legacy config."""
        if self.liteservers:
            return self.liteservers[0]
        return {
            "ip": int(self.liteserver_ip) if self.liteserver_ip is not None else 0,
            "port": int(self.liteserver_port) if self.liteserver_port is not None else 0,
            "id": {"@type": "pub.ed25519", "key": self.liteserver_pubkey or ""},
        }
    
    def liteclient_servers(self) -> List[Dict[str, Any]]:
        """Returns all configured liteservers for round-robin failover."""
        if self.liteservers:
            return self.liteservers
        # Fallback to legacy single server config
        return [self.liteclient_server()]

    @classmethod
    def from_env(cls) -> "Config":
        cfg = cls()
        # Logging
        cfg.loglevel = int(os.getenv("EMUSO_LOGLEVEL", 1))
        cspath = os.getenv("COLOR_SCHEMA_PATH", "").strip()
        cfg.color_schema_path = cspath or None
        if cfg.color_schema_path:
            with open(cfg.color_schema_path, "r") as f:
                cfg.color_schema = json.load(f)
        else:
            cfg.color_schema = None
            logger.warning("COLOR_SCHEMA is not defined")

        # Emulators
        cfg.emulator_path = os.getenv("EMULATOR_PATH")
        cfg.emulator_unchanged_path = os.getenv("EMULATOR_UNCHANGED_PATH")
        if not cfg.emulator_path:
            logger.error("EMULATOR_PATH is not set")
        elif not os.path.exists(cfg.emulator_path):
            logger.error(f"EMULATOR_PATH not found: {cfg.emulator_path}")
        else:
            logger.warning(f"Emulator primary: {cfg.emulator_path}")
        if not cfg.emulator_unchanged_path:
            logger.error("EMULATOR_UNCHANGED_PATH is not set")
        elif not os.path.exists(cfg.emulator_unchanged_path):
            logger.error(f"EMULATOR_UNCHANGED_PATH not found: {cfg.emulator_unchanged_path}")
        else:
            logger.warning(f"Emulator secondary: {cfg.emulator_unchanged_path}")
        cfg.c7_rewrite_raw = os.getenv("C7_REWRITE")
        try:
            cfg.c7_rewrite = json.loads(cfg.c7_rewrite_raw) if cfg.c7_rewrite_raw else None
        except Exception:
            cfg.c7_rewrite = None

        # Input filters
        cfg.txs_to_process_path = os.getenv("TXS_TO_PROCESS_PATH")
        if cfg.txs_to_process_path:
            try:
                with open(cfg.txs_to_process_path, "r") as f:
                    cfg.txs_to_process = json.load(f)
            except Exception:
                cfg.txs_to_process = None

        # Liteserver - support both legacy single server and multiple servers with suffixes
        cfg.liteservers = []
        
        # Try to load multiple liteservers with suffixes (_1, _2, _3, etc.)
        for i in range(1, 100):  # Support up to 99 servers (reasonable limit)
            suffix = f"_{i}"
            ls_ip = os.getenv(f"LITESERVER_SERVER{suffix}")
            ls_port = os.getenv(f"LITESERVER_PORT{suffix}")
            ls_pubkey = os.getenv(f"LITESERVER_PUBKEY{suffix}")
            
            if ls_ip and ls_port and ls_pubkey:
                server = {
                    "ip": int(ls_ip),
                    "port": int(ls_port),
                    "id": {"@type": "pub.ed25519", "key": ls_pubkey.strip('"')},
                }
                cfg.liteservers.append(server)
                logger.info(f"Loaded liteserver {i}: {ls_ip}:{ls_port}")
            else:
                # Stop when we hit the first gap in numbering
                break
        
        # Fallback to legacy single server config (no suffix) if no numbered servers found
        if not cfg.liteservers:
            ls_ip = os.getenv("LITESERVER_SERVER")
            cfg.liteserver_ip = int(ls_ip) if ls_ip is not None else None
            ls_port = os.getenv("LITESERVER_PORT")
            cfg.liteserver_port = int(ls_port) if ls_port is not None else None
            ls_pubkey = os.getenv("LITESERVER_PUBKEY")
            cfg.liteserver_pubkey = ls_pubkey.strip('"') if ls_pubkey else None
            if cfg.liteserver_ip and cfg.liteserver_port and cfg.liteserver_pubkey:
                logger.info(f"Using legacy single liteserver config: {cfg.liteserver_ip}:{cfg.liteserver_port}")
        else:
            logger.info(f"Loaded {len(cfg.liteservers)} liteservers with round-robin failover")
        
        cfg.liteserver_timeout = float(os.getenv("LITESERVER_TIMEOUT", 5))
        
        # seqno
        to_seq = os.getenv("TO_SEQNO")
        cfg.to_seqno = int(to_seq) if to_seq is not None else None
        from_seq = os.getenv("FROM_SEQNO")
        cfg.from_seqno = int(from_seq) if from_seq is not None else None
        cfg.to_emulate_mc_blocks = int(os.getenv("TO_EMULATE_MC_BLOCKS", 10))

        # Fix: properly parse booleans from strings
        only_mc = os.getenv("ONLYMC_BLOCK", "").lower()
        cfg.only_mc_blocks = only_mc in ("true", "1", "yes")
        parse_ls = os.getenv("PARSE_OVER_LS", "").lower()
        cfg.parse_over_ls = parse_ls in ("true", "1", "yes")

        # Performance
        cfg.nproc = int(os.getenv("NPROC", 10))
        cfg.chunk_size = int(os.getenv("CHUNK_SIZE", 2))
        cfg.tx_chunk_size = int(os.getenv("TX_CHUNK_SIZE", 40000))

        # Toncenter
        cfg.toncenter_api = os.getenv("TONCENTER_API", cfg.toncenter_api)
        cfg.toncenter_api_key = os.getenv("TONCENTER_API_KEY")
        cfg.toncenter_tx_hash = os.getenv("TONCENTER_TX_HASH")
        cfg.toncenter_msg_hash = os.getenv("TONCENTER_MSG_HASH")
        # Fix: properly parse boolean from string
        traces_env = os.getenv("TONCENTER_TRACES_BY_MASTERS", "").lower()
        cfg.toncenter_traces_by_masters = traces_env in ("true", "1", "yes")

        # Debug dumps
        cfg.debug_dumps_dir = os.getenv("DEBUG_DUMPS_DIR")
        return cfg

    def lcparams(self) -> Dict[str, Any]:
        """
        Returns LiteClient parameters with round-robin failover support.
        If multiple liteservers are configured, they will be tried in sequence.
        """
        return {
            'mode': 'roundrobin',
            'my_rr_servers': self.liteclient_servers(),
            'timeout': self.liteserver_timeout,
            'num_try': 3000,
            'threads': 1,
            'loglevel': max(self.loglevel - 3, 0)
        }
