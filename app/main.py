import uvicorn
import datetime
import os
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi_sqlalchemy import DBSessionMiddleware, db
from sqlalchemy import or_
from typing import Union
from soc_cars_core.schemas.admin_schema import CreateUser as SchemaCreateUser,ListUser as SchemaListUser, ListCar as SchemaListCar, \
UpdateUser as SchemaUpdateUser, RentCar as SchemaRentCar

from soc_cars_core.schemas.auth_schema import Token
from soc_cars_core.models import Car, User, UserRental

from soc_cars_core.utils import check_if_user_exists, find_days_between_dates
from soc_cars_core.auth import get_password_hash, authenticate_user, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, get_current_active_user
from typing import List, Annotated

from fastapi.security import OAuth2PasswordRequestForm


app = FastAPI(title="Car Renting User")

# to avoid csrftokenError
app.add_middleware(DBSessionMiddleware, db_url=os.environ['DATABASE_URL'])


@app.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends()
):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

  
@app.post('/user/create', response_model=SchemaListUser)
async def create_user(user:SchemaCreateUser):
    if check_if_user_exists(user.email):
        raise HTTPException(status_code=400, detail="User with this email already exist")
    pwd_hash = get_password_hash(user.password)
    db_user = User(name=user.name, email=user.email, phone_number=user.phone_number, address=user.address, password=pwd_hash)
    db.session.add(db_user)
    db.session.commit()
    return db_user


@app.get('/user/view', response_model=SchemaListUser)
async def user_view(
    current_user: User = Depends(get_current_active_user)
):
    return current_user


@app.patch('/user/update', response_model=SchemaListUser)
async def update_user(
    user_update: SchemaUpdateUser, current_user: User = Depends(get_current_active_user)
):
    user_data = user_update.dict(exclude_unset=True)
    for key, value in user_data.items():
        if value:
            setattr(current_user, key, value)
    db.session.add(current_user)
    db.session.commit()
    db.session.refresh(current_user)
    return current_user


@app.get('/cars', response_model=List[SchemaListCar])
async def list_cars(
    query: Union[str, None] = None, current_user: User = Depends(get_current_active_user)
):
    cars = db.session.query(Car)
    if query:
        cars = cars.filter(
            or_(
                Car.name.ilike('%{}%'.format(query)), 
                Car.car_type.ilike('%{}%'.format(query))
            ))
    return cars.all()


@app.post('/user/car/{car_id}/rent')
async def cars_rent(
    car_id: int, rent_car: SchemaRentCar, current_user: User = Depends(get_current_active_user)
):
    car = db.session.query(Car).get(car_id)

    if not car:
        raise HTTPException(status_code=404, detail="Cars gear not found")
    
    if car.available_count < rent_car.item_count:
        if car.available_count == 0:
            raise HTTPException(status_code=409, detail="Unable to process your request. This item is sold out") 
        raise HTTPException(status_code=409, detail="Unable to process your request. Only {} no of item you requested is available".format(car.available_count)) 

    user_rental = UserRental(user_id=current_user.id, car_id=car_id, rented_car_count=rent_car.item_count, user_requested_duration_in_days=rent_car.rental_duration)
    db.session.add(user_rental)

    car.available_count = car.available_count - rent_car.item_count
    db.session.add(car)
    
    db.session.commit()
    db.session.refresh(car)
    return user_rental.serialize()

@app.get('/user/car/rentals/view', )
async def user_rentals_view(
    current_user: User = Depends(get_current_active_user)
):
    past_rentals = db.session.query(UserRental).filter(UserRental.user_id == current_user.id, UserRental.rental_end_date.isnot(None)).order_by(UserRental.rental_started.desc())
    current_rentals = db.session.query(UserRental).filter(UserRental.user_id == current_user.id, UserRental.rental_end_date.is_(None)).order_by(UserRental.rental_started.desc())

    return {
        'past_rentals': [rental.serialize() for rental in past_rentals],
        'current_rentals': [rental.serialize() for rental in current_rentals]
    }

@app.get('/test')
async def test():
    return {'a': datetime.datetime.now(), 'b': datetime.datetime.utcnow()}


@app.put('/user/car/{user_rental_id}/return')
async def car_return(
    user_rental_id: int, current_user: User = Depends(get_current_active_user)
):
    user_rental = db.session.query(UserRental).filter(UserRental.id == user_rental_id, UserRental.user_id == current_user.id).first()
    if not user_rental:
        raise HTTPException(status_code=404, detail="User Rental not found")

    if user_rental.rental_end_date:
        raise HTTPException(status_code=400, detail="This item is already returned")
    
    total_rent_days = find_days_between_dates(user_rental.rental_started, datetime.datetime.now(datetime.timezone.utc))
    car = user_rental.car
    rent_per_day = user_rental.car.rent_per_day
    total_rent = total_rent_days * rent_per_day * user_rental.rented_car_count

    car.available_count = car.available_count + user_rental.rented_car_count

    user_rental.total_rent = total_rent

    db.session.add(user_rental)
    db.session.add(car)
    db.session.commit()
    db.session.refresh(user_rental)
    return user_rental.serialize()
