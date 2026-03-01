ethernal-backend/
│
├── api/                         
│   ├── __init__.py
│   ├── main.py                   
│   ├── config.py                 
│   │
│   ├── core/                     
│   │   ├── __init__.py
│   │   ├── auth.py              
│   │   ├── dependencies.py       
│   │   └── exceptions.py      
│   │
│   ├── db/                      
│   │   ├── __init__.py
│   │   ├── base.py              
│   │   ├── session.py           
│   │   │
│   │   ├── models/               
│   │   │   ├── __init__.py      
│   │   │   ├── user.py          
│   │   │   ├── fund.py         
│   │   │   ├── transaction.py  
│   │   │   ├── treasury.py     
│   │   │   ├── protocol.py      
│   │   │   └── faucet.py        
│   │   │
│   │   └── repositories/         
│   │       ├── __init__.py
│   │       ├── user_repo.py
│   │       ├── fund_repo.py
│   │       └── transaction_repo.py
│   │
│   ├── schemas/                 
│   │   ├── __init__.py
│   │   ├── user.py            
│   │   └── fund.py             
│   │
│   ├── services/                
│   │   ├── __init__.py
│   │   └── blockchain_service.py 
│   │
│   └── v1/                      
│       ├── __init__.py
│       └── routers/            
│           ├── __init__.py
│           ├── users.py         
│           ├── funds.py          
│           ├── treasury.py      
│           ├── protocols.py     
│           └── admin.py        
│
├── alembic/                    
│   ├── env.py                  
<<<<<<< HEAD
│   └── versions/                 ← archivos de migración (autogenerados)
│       └── 001_initial_schema.py ← se genera con: alembic revision --autogenerate
=======
│   └── versions/              
│       └── 001_initial_schema.py 
>>>>>>> 8e9be973a87422ba3ea8056cd8b7e82d00bb88bb
│
├── tests/                      
│   └── __init__.py
│
<<<<<<< HEAD
├── alembic.ini                   ← config de Alembic
├── pyproject.toml                ← Python 3.12.3 - igual agregar PYTHON-VERSION=3.12.3 como variable es mejor (sino Render toma 3.14.3)
├── requirements.txt              ← dependencias
├── .env.example                  ← template de variables de entorno
├── .gitignore
└── .python-version               ← 3.12.3 (para Render) mejor ponerlo en el Enviroment
=======
├── alembic.ini              
├── pyproject.toml               
├── requirements.txt           
├── .env.example                
├── .gitignore
└── .python-version             
>>>>>>> 8e9be973a87422ba3ea8056cd8b7e82d00bb88bb
