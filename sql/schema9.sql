create table if not exists users (
    id int unsigned not null auto_increment,
    deleted boolean not null default 0,
    username varchar(32) not null unique,
    serial char(20) not null,
    hash char(32) not null unique,
    reset_nonce varchar(32) default null,
    updated_on timestamp not null default current_timestamp on update current_timestamp,
    primary key(id)
    
) Engine=InnoDB default charset=utf8;

create table if not exists profiles (
    id int unsigned not null auto_increment,
    deleted boolean not null default 0,
    user_id int unsigned not null,
    ordinal tinyint not null default -1,
    name varchar(48) not null unique,  -- PES2009 names are 48 bytes
    `rank` int unsigned not null default 0,
    rating int unsigned not null default 0,
    points int unsigned not null default 0,
    disconnects int unsigned not null default 0,
    updated_on timestamp not null default current_timestamp on update current_timestamp,
    seconds_played bigint unsigned not null default 0,
    comment varchar(256) default null,
    primary key(id),
    foreign key(user_id) references users (id)

) Engine=InnoDB default charset=utf8;

create table if not exists matches (
    id bigint unsigned not null auto_increment,
    score_home int unsigned not null default 0,
    score_away int unsigned not null default 0,
    team_id_home int not null default -1,
    team_id_away int not null default -1,
    played_on timestamp not null default current_timestamp,
    friendly boolean not null default 0,  -- room setting (0x4310); friendlies don't count for ranking
    primary key(id)

) Engine=InnoDB default charset=utf8;

create table if not exists matches_played (
    id bigint unsigned not null auto_increment,
    match_id bigint unsigned not null,
    profile_id int unsigned not null,
    home boolean not null default 0,
    primary key(id),
    unique key(match_id, profile_id),
    foreign key(match_id) references matches (id),
    foreign key(profile_id) references profiles (id)

) Engine=InnoDB default charset=utf8;

create table if not exists streaks (
    id bigint unsigned not null auto_increment,
    profile_id int unsigned not null,
    wins int unsigned not null default 0,
    best int unsigned not null default 0,
    primary key(id),
    unique key(profile_id),
    foreign key(profile_id) references profiles (id)

) Engine=InnoDB default charset=utf8;

create table if not exists friends (
    id bigint unsigned not null auto_increment,
    profile_id int unsigned not null,
    friend_profile_id int unsigned not null,
    primary key(id),
    unique key(profile_id, friend_profile_id),
    foreign key(profile_id) references profiles (id),
    foreign key(friend_profile_id) references profiles (id)

) Engine=InnoDB default charset=utf8;

create table if not exists blocked (
    id bigint unsigned not null auto_increment,
    profile_id int unsigned not null,
    blocked_profile_id int unsigned not null,
    primary key(id),
    unique key(profile_id, blocked_profile_id),
    foreign key(profile_id) references profiles (id),
    foreign key(blocked_profile_id) references profiles (id)

) Engine=InnoDB default charset=utf8;

-- PES2009 profile settings, packet 0x4120 (sent back in 0x4103 at offset 422).
-- One column per field of the game's 28-byte block, in wire order. unk_*
-- fields are not identified yet but are stored so nothing is lost.
-- The profile comment lives in profiles.comment.
create table if not exists settings (
    profile_id int unsigned not null,
    unk_0 int unsigned not null default 0,
    birth_year smallint unsigned not null default 0,
    birth_month tinyint unsigned not null default 0,
    birth_day tinyint unsigned not null default 0,
    unk_8 tinyint unsigned not null default 0,
    country smallint unsigned not null default 0,
    unk_11 smallint unsigned not null default 0,
    fav_team smallint unsigned not null default 65535,  -- 0xffff = none
    fav_player int unsigned not null default 0,
    unk_19 tinyint unsigned not null default 0,
    unk_20 int unsigned not null default 0,
    unk_24 int unsigned not null default 0,
    updated_on timestamp not null default current_timestamp on update current_timestamp,
    primary key(profile_id),
    foreign key(profile_id) references profiles (id)

) Engine=InnoDB default charset=utf8;

